"""
WavToWemConverter - WAV to WEM conversion via Wwise.
"""
import sys
import os
import json
import subprocess
import tempfile
import shutil
import threading
import csv
import traceback
import time
from functools import partial
from datetime import datetime
from PyQt5 import QtWidgets, QtCore, QtGui, QtMultimedia
from PyQt5.QtCore import QObject, pyqtSignal, QThread
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import struct
from collections import namedtuple
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from editor.constants import (
    CuePoint, Label, startupinfo, CREATE_NO_WINDOW, current_version,
    MATPLOTLIB_AVAILABLE, PSUTIL_AVAILABLE
)
from editor.translations import TRANSLATIONS, tr


class WavToWemConverter(QtCore.QObject):
    progress_updated = QtCore.pyqtSignal(int)
    status_updated = QtCore.pyqtSignal(str, str) 
    conversion_finished = QtCore.pyqtSignal(list)
    
    SUPPORTED_SAMPLE_RATES = [48000, 44100, 36000, 32000, 28000, 24000, 22050, 
                              20000, 18000, 16000, 14000, 12000, 11025, 10000, 8000, 6000]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.file_pairs = []
        self.should_stop = False
        self.parent = parent
        self.wwise_path = ""
        self.project_path = ""
        self.output_folder = ""
        self.conversion_cache = {}
        self.adaptive_mode = False  
         
    def reset_state(self):
        """Reset converter state after stop or error"""
        self.should_stop = False
        self.status_updated.emit("Ready", "green")
        
        self.conversion_cache.clear()
        DEBUG.log("Conversion cache cleared")
        
        if self.output_folder and os.path.exists(self.output_folder):
            try:
                for file in os.listdir(self.output_folder):
                    if file.startswith("temp_") or file.startswith("best_") or file.startswith("test_"):
                        temp_file = os.path.join(self.output_folder, file)
                        try:
                            os.remove(temp_file)
                            DEBUG.log(f"Cleaned up temp file: {file}")
                        except:
                            pass
            except Exception as e:
                DEBUG.log(f"Error cleaning temp files: {e}", "WARNING")
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(script_dir, "temp_conversion")
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                DEBUG.log("Cleaned up temp_conversion directory")
            except Exception as e:
                DEBUG.log(f"Error cleaning temp_conversion: {e}", "WARNING")
    def convert_and_update_bnk(self, file_pair):
        try:
            wav_file = file_pair['wav_file']
            source_id = int(file_pair['file_id'])

            self.parent.append_conversion_log(f"Converting {os.path.basename(wav_file)} with maximum quality...")
            result_data = self.convert_with_quality(wav_file, 10)
            
            if not result_data:
                raise Exception("Failed to create WEM file with quality 10")

            new_wem_path = result_data['file']
            new_wem_size = result_data['size']
            self.parent.append_conversion_log(f"  ✓ Created WEM: {new_wem_size:,} bytes")

            self.parent.append_conversion_log("Searching and modifying BNK files...")
            bnk_files_info = self.parent.find_relevant_bnk_files()
            
            if not bnk_files_info:
                raise Exception("BNK Files for modifications not found in Wems")

            bnk_modified = False
            for bnk_path, bnk_type in bnk_files_info:

                original_editor = BNKEditor(bnk_path)
                if not original_editor.find_sound_by_source_id(source_id):
                    continue

                if bnk_type == 'sfx':
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.parent.base_path, "Wems", "SFX"))
                    mod_bnk_path = os.path.join(self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)
                else: # 'lang'
                    rel_path = os.path.relpath(bnk_path, os.path.join(self.parent.base_path, "Wems"))
                    mod_bnk_path = os.path.join(self.parent.mod_p_path, "OPP", "Content", "WwiseAudio", "Windows", rel_path)

                source_bnk_for_edit = mod_bnk_path
                if not os.path.exists(source_bnk_for_edit):
                    os.makedirs(os.path.dirname(source_bnk_for_edit), exist_ok=True)
                    shutil.copy2(bnk_path, source_bnk_for_edit)
                
                editor = BNKEditor(source_bnk_for_edit)
                
                if editor.modify_sound(source_id, new_size=new_wem_size, find_by_size=None):
                    editor.save_file()
                    self.parent.invalidate_bnk_cache(source_id)
                    self.parent.append_conversion_log(f"  ✓ Updated {os.path.basename(mod_bnk_path)}: ID {source_id} -> {new_wem_size} bytes")
                    bnk_modified = True
                    break

            if not bnk_modified:
                self.parent.append_conversion_log(f"  ✗ Warning: ID {source_id} not found in any BNK. Size not updated.", "WARNING")

            return {
                'success': True,
                'output_path': new_wem_path,
                'final_size': new_wem_size,
                'attempts': 1,
                'conversion': 'BNK Overwrite (Quality 10)',
                'size_diff_percent': 0
            }

        except Exception as e:
            DEBUG.log(f"Error converting and updating BNK: {e}", "ERROR")
            self.parent.append_conversion_log(f"  ✗ Error: {e}")
            return {'success': False, 'error': str(e)}
    def set_adaptive_mode(self, enabled):
        self.adaptive_mode = enabled
        
    def set_paths(self, wwise_path, project_path, output_folder):
        self.wwise_path = wwise_path
        self.project_path = project_path
        self.output_folder = output_folder
        self.conversion_cache.clear()
    def add_file_pair(self, audio_file, target_wem):
        if not os.path.exists(audio_file) or not os.path.exists(target_wem):
            return False

        audio_ext = os.path.splitext(audio_file)[1].lower()
        
        needs_conversion = audio_ext != '.wav'
        
        file_pair = {
            "audio_file": audio_file,
            "original_format": audio_ext,
            "needs_conversion": needs_conversion,
            "target_wem": target_wem,
            "audio_name": os.path.basename(audio_file),
            "target_name": os.path.basename(target_wem),
            "target_size": os.path.getsize(target_wem),
            "language": "",
            "file_id": os.path.splitext(os.path.basename(target_wem))[0]
        }
        
        self.file_pairs.append(file_pair)
        return True
        
    def clear_file_pairs(self):
        self.file_pairs.clear()
        
    def ensure_project_exists(self):
        
        if not self.wwise_path or not self.project_path:
            raise Exception("Please specify Wwise path and project path")
            
        project_dir = os.path.normpath(self.project_path)
        project_name = os.path.basename(project_dir)
        wproj_path = os.path.normpath(os.path.join(project_dir, f"{project_name}.wproj"))
        
        if os.path.exists(wproj_path):
            self.create_default_work_unit(project_dir)
            return wproj_path
            
        self.status_updated.emit("Creating Wwise project...", "blue")
        
        wwisecli_path = os.path.normpath(os.path.join(
            self.wwise_path, "Authoring", "x64", "Release", "bin", "WwiseCLI.exe"
        ))
        
        if not os.path.exists(wwisecli_path):
            raise FileNotFoundError(f"WwiseCLI.exe not found at: {wwisecli_path}")
        
        
        
        cmd = [
            wwisecli_path, f'"{wproj_path}"', "-CreateNewProject",
            "-Platform", "Windows", "-Quiet"
        ]
        os.removedirs(project_dir)
        result = subprocess.run(cmd, capture_output=True, text=True, shell=False, creationflags=CREATE_NO_WINDOW, encoding='utf-8', errors='ignore')

        if result.returncode != 0:
            raise Exception(f"Failed to create project: {result.stderr}")
        
        self.create_default_work_unit(project_dir)
        return wproj_path
        
    
    def create_default_work_unit(self, project_dir):
        """Create default work unit in project directory"""
        conversion_settings_dir = os.path.join(project_dir, "Conversion Settings")
        os.makedirs(conversion_settings_dir, exist_ok=True)
        
        project_wwu_path = os.path.join(conversion_settings_dir, "Default Work Unit.wwu")
        
        if os.path.exists(project_wwu_path):
            try:
                os.remove(project_wwu_path)
                DEBUG.log(f"Deleted existing work unit file: {project_wwu_path}")
            except Exception as e:
                DEBUG.log(f"Error deleting existing work unit file: {e}", "ERROR")
        

        if getattr(sys, 'frozen', False):

            base_dir = os.path.dirname(sys.executable)
        else:
     
            base_dir = os.path.dirname(os.path.abspath(__file__))

        data_wwu_path = os.path.join(base_dir, "data", "Default Work Unit.wwu")
        
        DEBUG.log(f"Base directory: {base_dir}") 
        DEBUG.log(f"Looking for work unit file at: {data_wwu_path}") 
        
        if os.path.exists(data_wwu_path):
            shutil.copy2(data_wwu_path, project_wwu_path)
            DEBUG.log(f"Copied work unit file from data to project")
        else:
            DEBUG.log(f"Work unit file not found in data directory: {data_wwu_path}", "ERROR")
            
    def resample_wav_file(self, input_wav, output_wav, target_sample_rate):
        """Resample WAV file to target sample rate using simple Python audio libraries"""
        try:
            import wave
            import struct
            import array
            
            with wave.open(input_wav, 'rb') as wav_in:
                params = wav_in.getparams()
                frames = wav_in.readframes(params.nframes)
                
                original_rate = params.framerate
                
                if original_rate == target_sample_rate:
              
                    shutil.copy2(input_wav, output_wav)
                    return True
                

                if params.sampwidth == 1:
                    fmt = 'B' 
                    samples = array.array(fmt, frames)
                elif params.sampwidth == 2:
                    fmt = 'h'  
                    samples = array.array(fmt, frames)
                elif params.sampwidth == 4:
                    fmt = 'i'  
                    samples = array.array(fmt, frames)
                else:
               
                    shutil.copy2(input_wav, output_wav)
                    return False
                
      
                ratio = target_sample_rate / original_rate
                new_length = int(len(samples) * ratio / params.nchannels) * params.nchannels
                
               
                resampled = array.array(fmt)
                
                for i in range(new_length):
                
                    orig_pos = i / ratio
                    orig_idx = int(orig_pos)
                    
                    if orig_idx < len(samples) - 1:
           
                        frac = orig_pos - orig_idx
                        val = samples[orig_idx] * (1 - frac) + samples[orig_idx + 1] * frac
                        resampled.append(int(val))
                    elif orig_idx < len(samples):
                        resampled.append(samples[orig_idx])
                    else:
                        resampled.append(0)
                
                with wave.open(output_wav, 'wb') as wav_out:
                    wav_out.setparams((
                        params.nchannels,
                        params.sampwidth,
                        target_sample_rate,
                        len(resampled) // params.nchannels,
                        params.comptype,
                        params.compname
                    ))
                    wav_out.writeframes(resampled.tobytes())
                
                return True
                
        except Exception as e:
            DEBUG.log(f"Resampling error: {e}", "ERROR")

            shutil.copy2(input_wav, output_wav)
            return False
            
    def create_wsources_file(self, path, wav_file, conversion_value=10):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        wav_path = os.path.normpath(wav_file)
        
        xml_content = f'''<?xml version="1.0" encoding="utf-8"?>
<ExternalSourcesList SchemaVersion="1" Root="{script_dir}">
    <Source Path="{wav_path}" Conversion="{conversion_value}"/>
</ExternalSourcesList>'''
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(xml_content)
            
    def convert_with_quality(self, wav_file, conversion_value):
        """Convert with detailed size logging"""
        cache_key = f"{wav_file}_{conversion_value}"
        if cache_key in self.conversion_cache:
            cached_result = self.conversion_cache[cache_key]
            DEBUG.log(f"Using cached result for Conversion={conversion_value}: {cached_result['size']:,} bytes")
            return cached_result
            
        script_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir = os.path.join(script_dir, "temp_conversion")
        os.makedirs(temp_dir, exist_ok=True)
        
        data_dir = os.path.join(script_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        wsources_path = os.path.join(data_dir, "convert.wsources")
        
        wav_size = os.path.getsize(wav_file)
        wav_name = os.path.basename(wav_file)
        DEBUG.log(f"Converting {wav_name} (input size: {wav_size:,} bytes) with Conversion={conversion_value}")
     
        if hasattr(self.parent, 'append_conversion_log'):
            self.parent.append_conversion_log(f"  → Testing Conversion={conversion_value} for {wav_name} (input: {wav_size:,} bytes)")
        
        self.create_wsources_file(wsources_path, wav_file, conversion_value)
        
        wwisecli_path = os.path.normpath(os.path.join(
            self.wwise_path, "Authoring", "x64", "Release", "bin", "WwiseCLI.exe"
        ))
        
        project_dir = os.path.normpath(self.project_path)
        project_name = os.path.basename(project_dir)
        wproj_path = os.path.normpath(os.path.join(project_dir, f"{project_name}.wproj"))
        
        cmd = [
            wwisecli_path, wproj_path, "-ConvertExternalSources", "Windows",
            wsources_path, "-ExternalSourcesOutput", temp_dir, "-Quiet"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, shell=False, creationflags=CREATE_NO_WINDOW, encoding='utf-8', errors='ignore')

        if result.returncode != 0:
            DEBUG.log(f"Conversion failed for Conversion={conversion_value}: {result.stderr}", "ERROR")
            if hasattr(self.parent, 'append_conversion_log'):
                self.parent.append_conversion_log(f"    ✗ Conversion={conversion_value} failed: {result.stderr}")
            raise Exception(f"Conversion error: {result.stderr}")
        
        wav_name_no_ext = os.path.splitext(wav_name)[0]
        wem_file = self.find_wem_file(temp_dir, wav_name_no_ext)
        
        if wem_file:
            file_size = os.path.getsize(wem_file)
            
            DEBUG.log(f"SUCCESS: Conversion={conversion_value} produced {file_size:,} bytes (ratio: {file_size/wav_size:.2f}x)")
                   
            if hasattr(self.parent, 'append_conversion_log'):
                self.parent.append_conversion_log(f"    ✓ Conversion={conversion_value} → {file_size:,} bytes")
            
            result_data = {
                'file': wem_file,
                'size': file_size,
                'dir': temp_dir,
                'conversion': conversion_value
            }
            self.conversion_cache[cache_key] = result_data
            return result_data
        else:
            DEBUG.log(f"No WEM file found after conversion with Conversion={conversion_value}", "ERROR")
            if hasattr(self.parent, 'append_conversion_log'):
                self.parent.append_conversion_log(f"    ✗ Conversion={conversion_value} - no output file")
                
        return None

    def find_wem_file(self, script_dir, wav_name):
        possible_paths = [
            os.path.join(script_dir, "Windows", f"{wav_name}.wem"),
            os.path.join(script_dir, f"{wav_name}.wem")
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        return None
        
    def increase_file_size(self, file_path, target_size_bytes):
        """Simple file size increase with logging"""
        if not os.path.exists(file_path):
            DEBUG.log(f"ERROR: File not found: {file_path}", "ERROR")
            return False
        
        current_size = os.path.getsize(file_path)
        DEBUG.log(f"increase_file_size: current={current_size:,}, target={target_size_bytes:,}")
        
        if target_size_bytes <= current_size:
            DEBUG.log(f"File already at or above target size ({current_size:,} >= {target_size_bytes:,})")
            return True
        
        bytes_to_add = target_size_bytes - current_size
        DEBUG.log(f"Adding {bytes_to_add:,} bytes of padding...")
        
        try:
            with open(file_path, 'ab') as file:
                file.write(b'\x00' * bytes_to_add)
            
            new_size = os.path.getsize(file_path)
            DEBUG.log(f"File size increased from {current_size:,} to {new_size:,} bytes")
            
            if new_size != target_size_bytes:
                DEBUG.log(f"WARNING: New size {new_size:,} != target {target_size_bytes:,}", "WARNING")
            
            return True
            
        except Exception as e:
            DEBUG.log(f"ERROR while increasing file size: {e}", "ERROR")
            return False
    def convert_single_file(self, file_pair):
        """Convert single file with stop checking"""
        
        if self.should_stop:
            return {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}
        
        try:
            wav_file = file_pair['wav_file']
            target_wem = file_pair['target_wem']
            target_size = file_pair['target_size']
            
            if self.should_stop:
                return {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}

            wav_info = self.get_wav_info(wav_file)
            if not wav_info:
                return {'success': False, 'error': 'Could not read WAV file info'}

            if self.should_stop:
                return {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}
       
            current_sample_rate = wav_info.get('sample_rate', 44100)
            target_sample_rate = current_sample_rate
            attempts = 0
            max_attempts = 5
            
            while attempts < max_attempts:
       
                if self.should_stop:
                    return {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}
                
                attempts += 1
                
                result = self.convert_with_sample_rate(wav_file, target_sample_rate, target_wem)
                
                if result.get('success'):
                    final_size = result.get('file_size', 0)
                    size_diff = abs(final_size - target_size) / target_size * 100
                    
                    return {
                        'success': True,
                        'output_path': result['output_path'],
                        'final_size': final_size,
                        'target_size': target_size,
                        'size_diff_percent': size_diff,
                        'sample_rate': target_sample_rate,
                        'attempts': attempts,
                        'resampled': target_sample_rate != current_sample_rate,
                        'conversion': f"{current_sample_rate}Hz → {target_sample_rate}Hz" if target_sample_rate != current_sample_rate else f"{current_sample_rate}Hz"
                    }
                
                if self.adaptive_mode and result.get('error', '').find('too large') != -1:
                    if target_sample_rate > 22050:
                        target_sample_rate = 22050
                    elif target_sample_rate > 16000:
                        target_sample_rate = 16000
                    elif target_sample_rate > 11025:
                        target_sample_rate = 11025
                    else:
                        break
                else:
                    break
            
            return result if result else {'success': False, 'error': 'Conversion failed after all attempts'}
            
        except Exception as e:
            return {'success': False, 'error': f'Exception during conversion: {str(e)}'}
    def get_wav_sample_rate(self, wav_file):
        """Get sample rate from WAV file"""
        try:
            import wave
            with wave.open(wav_file, 'rb') as wav:
                return wav.getframerate()
        except Exception as e:
            DEBUG.log(f"Error reading WAV sample rate: {e}", "ERROR")
            return 48000 
    def convert_single_file_main(self, file_pair, file_index, total_files):
        if self.should_stop:
            return {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}

        try:
            audio_file = file_pair.get('audio_file') or file_pair.get('wav_file')
            if not audio_file:
                return {'success': False, 'error': 'Audio file not specified in file_pair'}
            
            audio_ext = os.path.splitext(audio_file)[1].lower()
            needs_conversion = file_pair.get('needs_conversion', False) or (audio_ext != '.wav')

            audio_name = file_pair.get('audio_name') or file_pair.get('wav_name', '')
            
            original_filename = os.path.splitext(audio_name)[0] if audio_name else os.path.splitext(os.path.basename(audio_file))[0]
            
            file_id = file_pair.get('file_id', '')
            is_id_name = original_filename.isdigit() and file_id and original_filename == file_id
            
            if is_id_name:
                found_entry = next((entry for entry in self.parent.all_files if entry.get("Id", "") == file_id), None)
                if found_entry:
                    shortname = found_entry.get("ShortName", "")
                    original_filename = os.path.splitext(shortname)[0]
                    DEBUG.log(f"Found original name for ID {file_id}: {original_filename}")

            DEBUG.log(f"Original name for Wwise: {original_filename}")
            DEBUG.log(f"AudioFile: {audio_file}")
            
            if needs_conversion:
                self.status_updated.emit(f"Converting {original_filename} to WAV...", "blue")
                audio_converter = getattr(self.parent, 'audio_to_wav_converter', AudioToWavConverter())
                
                if not audio_converter.is_available():
                    return {'success': False, 'error': 'FFmpeg not found. Please install FFmpeg to convert audio formats.'}
                
                temp_dir = tempfile.mkdtemp(prefix="audio_convert_")
                temp_wav = os.path.join(temp_dir, f"{original_filename}.wav")
                success, result = audio_converter.convert_to_wav(audio_file, temp_wav)
                
                if not success:
                    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
                    return {'success': False, 'error': f'Error converting to WAV: {result}'}
                
                wav_file = temp_wav
                file_pair['temp_wav'] = temp_wav
                file_pair['temp_dir'] = temp_dir
                DEBUG.log(f"Converted {original_filename} from {audio_ext} to WAV: {temp_wav}")
            else:
                current_wav_name = os.path.basename(audio_file)
                expected_wav_name = f"{original_filename}.wav"
                
                if current_wav_name != expected_wav_name:
                    temp_dir = tempfile.mkdtemp(prefix="wav_rename_")
                    temp_wav = os.path.join(temp_dir, expected_wav_name)
                    shutil.copy2(audio_file, temp_wav)
                    wav_file = temp_wav
                    file_pair['temp_wav'] = temp_wav
                    file_pair['temp_dir'] = temp_dir
                    DEBUG.log(f"WAV renamed for Wwise: {current_wav_name} -> {expected_wav_name}")
                else:
                    wav_file = audio_file
            
            updated_file_pair = file_pair.copy()
            updated_file_pair['wav_file'] = wav_file
            updated_file_pair['wav_name'] = f"{original_filename}.wav"
            
            conversion_method = self.parent.settings.data.get("conversion_method", "adaptive")
            
            if conversion_method == "bnk":
                DEBUG.log(f"Using method: BNK overwrite for {original_filename}")
                result = self.convert_and_update_bnk(updated_file_pair)
            else: # "adaptive"
                DEBUG.log(f"Using method: Adaptive Conversion for {original_filename}")
                if self.adaptive_mode:
                    result = self.convert_single_file_adaptive(updated_file_pair, file_index, total_files)
                else:
                    target_size = file_pair['target_size']
                    result = self.try_conversion_with_binary_search(wav_file, target_size, file_index, total_files, original_filename)
            
            if result.get('success') and is_id_name:
                output_path = result['output_path']
                id_output_path = os.path.join(os.path.dirname(output_path), f"{file_id}.wem")
                if os.path.exists(output_path) and output_path != id_output_path:
                    shutil.move(output_path, id_output_path)
                    result['output_path'] = id_output_path
                    DEBUG.log(f"Final WEM renamed back to ID: {output_path} -> {id_output_path}")

            return result
                
        except Exception as e:
            DEBUG.log(f"Error in convert_single_file_main: {e}", "ERROR")
            return {'success': False, 'error': f'Error while converting: {str(e)}'}
        finally:
            if 'temp_dir' in file_pair and os.path.exists(file_pair['temp_dir']):
                try:
                    shutil.rmtree(file_pair['temp_dir'])
                    DEBUG.log(f"Cleared temp directory: {file_pair['temp_dir']}")
                except Exception as e:
                    DEBUG.log(f"Failed to clean up temporary directory: {e}", "WARNING")

    def convert_single_file_adaptive(self, file_pair, file_index, total_files):
        """Adaptive conversion with sample rate adjustment"""
        if self.should_stop:
            return {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}
        
        try:
            wav_file = file_pair['wav_file']
            target_size = file_pair['target_size']
            wav_name = os.path.splitext(os.path.basename(wav_file))[0]
            
            DEBUG.log(f"Starting adaptive conversion for {wav_name}")
            
            original_sample_rate = self.get_wav_sample_rate(wav_file)
            DEBUG.log(f"Original sample rate: {original_sample_rate}Hz")
            
            try:
                result = self.try_conversion_with_binary_search(wav_file, target_size, file_index, total_files, wav_name)
                if result.get('success'):
                    result['resampled'] = False
                    result['sample_rate'] = original_sample_rate
                    result['conversion'] = f"{original_sample_rate}Hz (original)"
                    return result
            except Exception as e:
                DEBUG.log(f"Original quality conversion failed: {e}")
            
            optimal_rate_idx = self.find_optimal_sample_rate(wav_file, target_size, file_index, total_files, wav_name)
            
            if optimal_rate_idx >= 0:
                optimal_rate = self.SUPPORTED_SAMPLE_RATES[optimal_rate_idx]
                
                if optimal_rate < original_sample_rate: 
                    DEBUG.log(f"Using reduced sample rate: {optimal_rate}Hz (from {original_sample_rate}Hz)")
                    
                    temp_wav = os.path.join(self.output_folder, f"resampled_{wav_name}_{optimal_rate}.wav")
                    if not self.resample_wav_file(wav_file, temp_wav, optimal_rate):
                        return {'success': False, 'error': 'Failed to resample audio file'}
                    
                    result = self.try_conversion_with_binary_search(temp_wav, target_size, file_index, total_files, wav_name)
                    
                    if os.path.exists(temp_wav):
                        try:
                            os.remove(temp_wav)
                        except:
                            pass
                    
                    if result.get('success'):
                        result['resampled'] = True
                        result['sample_rate'] = optimal_rate
                        result['conversion'] = f"{original_sample_rate}Hz → {optimal_rate}Hz"
                    
                    return result
                else:
                    DEBUG.log(f"Optimal rate {optimal_rate}Hz is not lower than original {original_sample_rate}Hz")
            
            return {'success': False, 'error': 'Could not find suitable sample rate for target size'}
            
        except Exception as e:
            DEBUG.log(f"Error in adaptive conversion: {e}", "ERROR")
            return {'success': False, 'error': f'Adaptive conversion error: {str(e)}'}
    def find_optimal_sample_rate(self, wav_file, target_size, file_index, total_files, wav_name):
        """Binary search to find optimal sample rate for target size"""
        
        original_sample_rate = self.get_wav_sample_rate(wav_file)
        DEBUG.log(f"Original sample rate: {original_sample_rate}Hz")
        
        valid_rates = [rate for rate in self.SUPPORTED_SAMPLE_RATES if rate <= original_sample_rate]
        
        if not valid_rates:
            DEBUG.log(f"No valid sample rates found for original rate {original_sample_rate}Hz")
            return -1
        
        DEBUG.log(f"Valid sample rates for search: {valid_rates}")
        
        left, right = 0, len(valid_rates) - 1
        best_idx = -1
        
        while left <= right:
            mid = (left + right) // 2
            sample_rate = valid_rates[mid]
            
            self.status_updated.emit(
                f"File {file_index}/{total_files}: {wav_name} - Testing {sample_rate}Hz...", 
                "blue"
            )
            
            temp_wav = os.path.join(self.output_folder, f"test_{wav_name}_{sample_rate}.wav")
            if not self.resample_wav_file(wav_file, temp_wav, sample_rate):
                left = mid + 1
                continue
            
            try:
                result = self.convert_with_quality(temp_wav, -2)  
                
                if result and result['size'] <= target_size:
                    best_idx = mid
                    right = mid - 1 
                else:
                    left = mid + 1
                    
            except Exception as e:
                DEBUG.log(f"Error testing sample rate {sample_rate}: {e}", "ERROR")
                left = mid + 1
            finally:
                try:
                    os.remove(temp_wav)
                except:
                    pass
        
        if best_idx >= 0:
            best_rate = valid_rates[best_idx]
            original_idx = self.SUPPORTED_SAMPLE_RATES.index(best_rate)
            DEBUG.log(f"Found optimal sample rate: {best_rate}Hz (original: {original_sample_rate}Hz)")
            return original_idx
        
        return -1
    def try_conversion_with_binary_search(self, wav_file, target_size, file_index, total_files, original_filename):
        """Binary search with file copy to prevent cache corruption"""
        left, right = -2, 10
        best_result = None
        attempts = 0
        
        DEBUG.log(f"\n=== BINARY SEARCH START for {original_filename} ===")
        DEBUG.log(f"Target size: {target_size:,} bytes")
        DEBUG.log(f"Search range: [{left}, {right}]")
        DEBUG.log(f"WAV file: {wav_file}")
        
        if hasattr(self.parent, 'append_conversion_log'):
            self.parent.append_conversion_log(f"\n📊 Binary search for {original_filename}:")
            self.parent.append_conversion_log(f"   Target size: {target_size:,} bytes")
        
        all_attempts = []
        
        while left <= right:
            mid = (left + right) // 2
            attempts += 1
            
            DEBUG.log(f"\nAttempt {attempts}: Testing Conversion={mid} (range: [{left}, {right}])")
            
            self.status_updated.emit(
                f"File {file_index}/{total_files}: {original_filename} - attempt {attempts} (Conversion={mid})", 
                "blue"
            )
            
            try:
                result = self.convert_with_quality(wav_file, mid)
                
                if not result or not result.get('size'):
                    DEBUG.log(f"  → No result for Conversion={mid}")
                    all_attempts.append({'conversion': mid, 'size': None, 'status': 'failed'})
                    right = mid - 1
                    continue
                    
                current_size = result['size']
                size_ratio = current_size / target_size
                
                DEBUG.log(f"  → Result: {current_size:,} bytes ({size_ratio:.1%} of target)")
                
                attempt_info = {
                    'conversion': mid,
                    'size': current_size,
                    'ratio': size_ratio,
                    'status': 'ok' if current_size <= target_size else 'too_large'
                }
                all_attempts.append(attempt_info)
                
                if current_size <= target_size:
                    DEBUG.log(f"  → Acceptable size! Saving as best result")
                    
                    temp_best_file = os.path.join(self.output_folder, f"best_{original_filename}_{mid}.wem")
                    os.makedirs(self.output_folder, exist_ok=True)
                    shutil.copy2(result['file'], temp_best_file)
                    
                    best_result = {
                        'file': temp_best_file, 
                        'size': current_size,
                        'conversion': mid
                    }
                    
                    DEBUG.log(f"  → Copied best result to: {temp_best_file}")
                    
                    left = mid + 1 
                else:
                    DEBUG.log(f"  → Too large! Reducing quality")
                    right = mid - 1
                    
            except Exception as e:
                DEBUG.log(f"  → ERROR: {e}", "ERROR")
                all_attempts.append({'conversion': mid, 'size': None, 'status': 'error', 'error': str(e)})
                right = mid - 1
        
        DEBUG.log(f"\n=== BINARY SEARCH COMPLETE ===")
        DEBUG.log(f"Total attempts: {attempts}")
        DEBUG.log(f"All attempts summary:")
        for attempt in all_attempts:
            if attempt['size']:
                DEBUG.log(f"  Conversion={attempt['conversion']}: {attempt['size']:,} bytes ({attempt['status']})")
            else:
                DEBUG.log(f"  Conversion={attempt['conversion']}: FAILED ({attempt['status']})")
        
        if hasattr(self.parent, 'append_conversion_log'):
            self.parent.append_conversion_log(f"   Search complete after {attempts} attempts")
        
        if best_result:
            DEBUG.log(f"\nBest result: Conversion={best_result['conversion']}, size={best_result['size']:,} bytes")
            DEBUG.log(f"Best result file: {best_result['file']}")
            
            if not os.path.exists(best_result['file']):
                DEBUG.log(f"ERROR: Best result file disappeared: {best_result['file']}", "ERROR")
                return {'success': False, 'error': 'Best result file not found'}
            
            current_file_size = os.path.getsize(best_result['file'])
            DEBUG.log(f"Best result file current size: {current_file_size:,} bytes")
            
            if current_file_size != best_result['size']:
                DEBUG.log(f"WARNING: File size changed! Expected {best_result['size']:,}, got {current_file_size:,}", "WARNING")
                best_result['size'] = current_file_size 

            padding_needed = target_size - best_result['size']
            DEBUG.log(f"Padding needed: {padding_needed:,} bytes")
            
            if hasattr(self.parent, 'append_conversion_log'):
                self.parent.append_conversion_log(f"   Best: Conversion={best_result['conversion']} → {best_result['size']:,} bytes")
                self.parent.append_conversion_log(f"   Adding {padding_needed:,} bytes padding...")
            
            success = self.increase_file_size(best_result['file'], target_size)
            
            if success:
                final_size_after_padding = os.path.getsize(best_result['file'])
                DEBUG.log(f"File size after padding: {final_size_after_padding:,} bytes")
                
                output_filename = f"{original_filename}.wem"
                output_path = os.path.join(self.output_folder, output_filename)
                
                counter = 1
                while os.path.exists(output_path) and output_path != best_result['file']:
                    output_filename = f"{original_filename}_{counter}.wem"
                    output_path = os.path.join(self.output_folder, output_filename)
                    counter += 1
                
                if output_path != best_result['file']:
                    shutil.copy2(best_result['file'], output_path)
                    DEBUG.log(f"Copied to final output: {output_path}")
                else:
                    DEBUG.log(f"Final output is same as best result file: {output_path}")
                
                final_size = os.path.getsize(output_path)
                size_difference = abs(final_size - target_size)
                size_percentage = (size_difference / target_size) * 100
                
                DEBUG.log(f"FINAL: Output file {output_filename} = {final_size:,} bytes (target was {target_size:,})")
                
                if final_size != target_size:
                    DEBUG.log(f"WARNING: Final size mismatch! Difference: {size_difference:,} bytes ({size_percentage:.1f}%)", "WARNING")
                
                if hasattr(self.parent, 'append_conversion_log'):
                    if final_size == target_size:
                        self.parent.append_conversion_log(f"   ✅ Success! Final size: {final_size:,} bytes (exact match)")
                    else:
                        self.parent.append_conversion_log(f"   ⚠️ Final size: {final_size:,} bytes (diff: {size_difference:,} bytes)")
                
                if best_result['file'] != output_path and os.path.exists(best_result['file']):
                    try:
                        os.remove(best_result['file'])
                        DEBUG.log(f"Cleaned up temporary file: {best_result['file']}")
                    except:
                        pass
                
                return {
                    'success': True,
                    'output_path': output_path,
                    'final_size': final_size,
                    'attempts': attempts,
                    'conversion': best_result.get('conversion', 0),
                    'size_diff_percent': size_percentage
                }
            else:
                DEBUG.log("Failed to adjust file size!", "ERROR")
                return {'success': False, 'error': 'Failed to adjust file size'}
        else:
            DEBUG.log("\nNo acceptable result found!", "ERROR")
            
            try:
                min_result = self.convert_with_quality(wav_file, -2)
                if min_result and min_result.get('size'):
                    min_size = min_result['size']
                    if min_size > target_size:
                        size_diff = ((min_size - target_size) / target_size) * 100
                        DEBUG.log(f"Minimum possible size: {min_size:,} bytes ({size_diff:.1f}% over target)", "ERROR")
                        
                        if hasattr(self.parent, 'append_conversion_log'):
                            self.parent.append_conversion_log(f"   ❌ Failed! Minimum size {min_size:,} > target {target_size:,}")
                        
                        return {
                            'success': False, 
                            'error': f'Cannot achieve target size. Minimum: {min_size:,} bytes, Target: {target_size:,} bytes ({size_diff:.1f}% over). Try reducing WAV quality or using adaptive mode.',
                            'size_warning': True
                        }
            except Exception as e:
                DEBUG.log(f"Error testing minimum quality: {e}", "ERROR")

            return {'success': False, 'error': 'Failed to create WEM file. Please check your WAV file and Wwise configuration. Try choosing a different Wwise project folder.'}
    def convert_single_file(self, file_pair, file_index, total_files):
        """Main conversion method that chooses between adaptive or normal mode"""
        if self.should_stop:
            return {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}
        if self.adaptive_mode:
            return self.convert_single_file_adaptive(file_pair, file_index, total_files)
        else:
      
            wav_file = file_pair['wav_file']
            target_size = file_pair['target_size']
            wav_name = os.path.splitext(os.path.basename(wav_file))[0]
            
            return self.try_conversion_with_binary_search(wav_file, target_size, file_index, total_files, wav_name)
            
    def convert_all_files(self):
            """Convert all files with stop checking"""
            try:
                self.should_stop = False
                results = []
                total_files = len(self.file_pairs)
                
                if total_files == 0:
                    self.conversion_finished.emit([])
                    return
                
                self.conversion_cache.clear()
                DEBUG.log("Starting conversion - cache cleared")
                
                try:
                    wproj_path = self.ensure_project_exists()
                    
                    DEBUG.log(f"Using Wwise project: {wproj_path}")
                except Exception as e:
                    error_result = {
                        'file_pair': {'audio_name': 'Project Setup'},
                        'result': {'success': False, 'error': f'Failed to setup Wwise project: {str(e)}'}
                    }
                    self.conversion_finished.emit([error_result])
                    return
                
                for i, file_pair in enumerate(self.file_pairs):
           
                    if self.should_stop:
                        DEBUG.log(f"Conversion stopped at file {i+1}/{total_files}")
                    
                        for j in range(i, total_files):
                            results.append({
                                'file_pair': self.file_pairs[j],
                                'result': {'success': False, 'stopped': True, 'error': 'Conversion stopped by user'}
                            })
                        break
                    
                    progress = int((i / total_files) * 100)
                    self.progress_updated.emit(progress)
                    self.status_updated.emit(f"Converting {i+1}/{total_files}: {file_pair['audio_name']}", "blue")
                    
                    result = self.convert_single_file_main(file_pair, i+1, total_files)
                    results.append({
                        'file_pair': file_pair,
                        'result': result
                    })
                    
                    if self.should_stop:
                        break
                
                self.conversion_finished.emit(results)
                
            except Exception as e:
                DEBUG.log(f"Error in convert_all_files: {e}", "ERROR")
                error_result = {
                    'file_pair': {'wav_name': 'Unknown'},
                    'result': {'success': False, 'error': f'Conversion thread error: {str(e)}'}
                }
                self.conversion_finished.emit([error_result])
        
    def cleanup_temp_directories(self, temp_dirs):
        self.status_updated.emit("Cleaning up temporary files...", "blue")
        
        for temp_dir in temp_dirs:
            try:
                if os.path.exists(temp_dir) and "temp" in temp_dir.lower():
                    shutil.rmtree(temp_dir)
            except Exception as e:
                DEBUG.log(f"Failed to delete temp folder {temp_dir}: {e}", "WARNING")
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(script_dir, "data")
        wsources_file = os.path.join(data_dir, "convert.wsources")
        if os.path.exists(wsources_file):
            try:
                os.remove(wsources_file)
            except:
                pass
    def stop_conversion(self):
        """Signal the conversion process to stop"""
        self.should_stop = True
        self.status_updated.emit("Stopping conversion...", "orange")
        
        self.conversion_cache.clear()
        DEBUG.log("Conversion stopped - cache cleared")
    
 
