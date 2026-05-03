import asyncio
import os
import re
import aiohttp
import pandas as pd
import io
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')

# --- КОНСТАНТЫ ---
UNIVERSITY_URL = "https://www.rsatu.ru"
SCHEDULE_PAGE = f"{UNIVERSITY_URL}/students/raspisanie-zanyatiy/"

# Запасные ссылки
FALLBACK_CORRESPONDENCE_URL = "https://www.rsatu.ru/upload/iblock/852/2ds339alfi82bm6snybkazc8y4prn6dd/Raspisanie-vesenney-ustanovochnoy-sessii-25_26-uch.g.-dlya-zaochnikov-23.01.2026.xlsx"
FALLBACK_CONSULTATIONS_URL = "https://www.rsatu.ru/upload/iblock/69a/lp9v92s82rnuiuxqdhjqjqog55yroon4/Raspisanie-konsultatsiy-zaochnikov-03.04.2026.xlsx"

# --- ПАТТЕРНЫ ДЛЯ ПОИСКА ---
GROUP_PATTERN = re.compile(r'([А-Я]{2,4}\d?[к]?-\d{2,3})(?:[-\/](\d+))?')
TEACHER_PATTERN = re.compile(r'([А-Я][а-я]+)\s+([А-Я]\.[А-Я]\.)')
ROOM_PATTERN = re.compile(r'[А-Я]-\d{3}|\d{1,2}-\d{3}|\b\d{3}\b|Большой спортзал|Малый спортзал')
TYPE_PATTERN = re.compile(r'\b(Л|П|ЛР)\b')
CORRESPONDENCE_PATTERN = re.compile(r'^[З3]', re.IGNORECASE)

# --- СОСТОЯНИЯ ---
class States(StatesGroup):
    waiting_for_user_type = State()
    waiting_for_group = State()
    waiting_for_subgroup = State()
    waiting_for_teacher_name = State()
    waiting_for_teacher_day = State()
    waiting_for_day = State()

# --- ХРАНЕНИЕ ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ ---
user_data = {}

# --- ДНИ НЕДЕЛИ ---
RUSSIAN_DAYS = {
    0: "Понедельник",
    1: "Вторник", 
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье"
}

DAYS = {
    "Понедельник": "ПН",
    "Вторник": "ВТ",
    "Среда": "СР",
    "Четверг": "ЧТ",
    "Пятница": "ПТ",
    "Суббота": "СБ"
}

DAY_NAMES = {
    "ПН": "ПОНЕДЕЛЬНИК",
    "ВТ": "ВТОРНИК",
    "СР": "СРЕДА",
    "ЧТ": "ЧЕТВЕРГ",
    "ПТ": "ПЯТНИЦА",
    "СБ": "СУББОТА"
}

# --- ФУНКЦИЯ ОПРЕДЕЛЕНИЯ НЕДЕЛИ С НОМЕРОМ ---
def get_academic_week_number(date=None):
    if date is None:
        date = datetime.now()
    
    year = date.year
    if date.month >= 9:
        semester_start = datetime(year, 9, 1)
    elif date.month >= 2:
        semester_start = datetime(year, 2, 1)
    else:
        semester_start = datetime(year - 1, 2, 1)
    
    if date < semester_start:
        if date.month >= 2:
            semester_start = datetime(year - 1, 9, 1)
        else:
            semester_start = datetime(year - 1, 2, 1)
    
    days_diff = (date - semester_start).days
    
    if days_diff < 0:
        if semester_start.month == 9:
            semester_start = datetime(year, 2, 1)
        else:
            semester_start = datetime(year, 9, 1)
        days_diff = (date - semester_start).days
    
    if days_diff < 0:
        week_num = 1
    else:
        week_num = days_diff // 7 + 1
    
    return week_num

def get_week_type(week_offset=0):
    start_date = datetime(2024, 9, 1)
    current_date = datetime.now()
    target_date = current_date + timedelta(weeks=week_offset)
    week_number = get_academic_week_number(target_date)
    
    days_diff = (target_date - start_date).days
    
    if days_diff < 0:
        start_date = datetime(2023, 9, 1)
        days_diff = (target_date - start_date).days
    
    weeks_diff = days_diff // 7
    
    if weeks_diff % 2 == 0:
        if week_offset == 0:
            week_display = f"🔴 НЕЧЕТНАЯ НЕДЕЛЯ (№{week_number})"
        elif week_offset == 1:
            week_display = f"🔴 СЛЕДУЮЩАЯ НЕДЕЛЯ - НЕЧЕТНАЯ (№{week_number})"
        else:
            week_display = f"🔴 ПРЕДЫДУЩАЯ НЕДЕЛЯ - НЕЧЕТНАЯ (№{week_number})"
        return week_display, 2, 43
    else:
        if week_offset == 0:
            week_display = f"🔵 ЧЕТНАЯ НЕДЕЛЯ (№{week_number})"
        elif week_offset == 1:
            week_display = f"🔵 СЛЕДУЮЩАЯ НЕДЕЛЯ - ЧЕТНАЯ (№{week_number})"
        else:
            week_display = f"🔵 ПРЕДЫДУЩАЯ НЕДЕЛЯ - ЧЕТНАЯ (№{week_number})"
        return week_display, 44, 85

def get_day_start_row(week_start, day_name):
    day_offsets = {
        "Понедельник": 0,
        "Вторник": 7,
        "Среда": 14,
        "Четверг": 21,
        "Пятница": 28,
        "Суббота": 35
    }
    offset = day_offsets.get(day_name, 0)
    return week_start + offset

def get_today_name():
    today_num = datetime.now().weekday()
    return RUSSIAN_DAYS[today_num]

def get_date_for_day(day_name, week_offset=0):
    today = datetime.now()
    today_num = today.weekday()
    
    day_map = {
        "Понедельник": 0,
        "Вторник": 1,
        "Среда": 2,
        "Четверг": 3,
        "Пятница": 4,
        "Суббота": 5
    }
    
    target_num = day_map.get(day_name, 0)
    days_diff = target_num - today_num + (week_offset * 7)
    
    target_date = today + timedelta(days=days_diff)
    return target_date.strftime("%d.%m.%Y")

# --- ВРЕМЯ ПАР ---
LESSON_TIMES = {
    1: "08:30-10:05",
    2: "10:15-11:50",
    3: "12:40-14:15",
    4: "14:25-16:00",
    5: "16:10-17:45",
    6: "17:55-19:30"
}

# --- ТИПЫ ЗАНЯТИЙ ---
LESSON_TYPES = {
    'Л': 'Лекция',
    'П': 'Практика',
    'ЛР': 'Лабораторная работа'
}

# --- ОСНОВНОЙ КЛАСС ---
class ScheduleMaster:
    def __init__(self):
        self.session = None
        self.fulltime_groups = []
        self.fulltime_base_groups = []
        self.fulltime_subgroups = {}
        self.fulltime_positions = {}
        self.fulltime_dfs = {}
        self.correspondence_groups = []
        self.correspondence_positions = {}
        self.correspondence_dfs = {}
        self.correspondence_lessons = {}
        self.consultations = {}
        self.exam_sessions = {}
        self.teacher_lessons = []
        self.room_lessons = []
        self.last_update = None
    
    async def start(self):
        self.session = aiohttp.ClientSession()
    
    async def stop(self):
        if self.session:
            await self.session.close()
    
    async def fetch_page(self, url):
        try:
            async with self.session.get(url, timeout=15, ssl=False) as response:
                return await response.text() if response.status == 200 else None
        except Exception as e:
            print(f"Ошибка загрузки {url}: {e}")
            return None
    
    async def find_excel_links(self, url):
        html = await self.fetch_page(url)
        if not html:
            return []
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        excel_links = []
        for link in soup.find_all('a', href=True):
            if '.xlsx' in link['href'].lower():
                href = link['href']
                full_url = href if href.startswith('http') else f"{UNIVERSITY_URL}{href}"
                excel_links.append(full_url)
        return excel_links
    
    async def get_latest_excel(self, page_url):
        excel_links = await self.find_excel_links(page_url)
        if not excel_links:
            return None
        return excel_links[0]
    
    async def download_excel(self, url):
        try:
            async with self.session.get(url, timeout=30, ssl=False) as response:
                if response.status == 200:
                    return await response.read()
                else:
                    print(f"Ошибка скачивания: статус {response.status}")
                    return None
        except Exception as e:
            print(f"Ошибка скачивания: {e}")
            return None
    
    async def find_correspondence_links(self):
        html = await self.fetch_page(SCHEDULE_PAGE)
        if not html:
            return None, None
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        correspondence_url = None
        consultations_url = None
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            text = link.get_text().lower()
            
            if ('заочн' in text or 'заочное' in text) and ('.xlsx' in href or '.xls' in href):
                full_url = href if href.startswith('http') else f"{UNIVERSITY_URL}{href}"
                correspondence_url = full_url
                print(f"✅ Найдено расписание заочников: {full_url}")
            
            if ('консультац' in text or 'конс' in text) and ('.xlsx' in href or '.xls' in href):
                full_url = href if href.startswith('http') else f"{UNIVERSITY_URL}{href}"
                consultations_url = full_url
                print(f"✅ Найдены консультации: {full_url}")
        
        return correspondence_url, consultations_url
    
    def clean_value(self, value):
        if pd.isna(value):
            return ""
        if isinstance(value, (int, float, np.integer, np.floating)):
            return str(int(value)) if value == int(value) else str(value)
        return str(value).strip()
    
    def parse_group_name(self, group_str):
        match = GROUP_PATTERN.search(group_str)
        if match:
            base_group = match.group(1)
            subgroup = match.group(2)
            return base_group, int(subgroup) if subgroup else None
        return None, None
    
    def is_correspondence_group(self, group_name):
        return bool(CORRESPONDENCE_PATTERN.match(group_name))
    
    def normalize_group_name(self, group_name):
        normalized = group_name.strip().upper()
        normalized = normalized.replace('Z', 'З')
        return normalized
    
    def remove_all_groups(self, text):
        groups = re.findall(r'[А-Я]{2,4}\d?[к]?-\d{2,3}(?:[-\/]\d+)?', text)
        for group in groups:
            text = text.replace(group, "")
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    def split_lessons_in_cell(self, cell_text):
        lines = cell_text.split('\n')
        
        if len(lines) > 1:
            lessons = []
            for line in lines:
                line = line.strip()
                if line and line not in ["", "nan", "—"]:
                    lessons.append(line)
            return lessons
        else:
            group_matches = list(GROUP_PATTERN.finditer(cell_text))
            if len(group_matches) > 1:
                lessons = []
                for match in group_matches:
                    start_pos = max(0, match.start() - 50)
                    lesson_text = cell_text[start_pos:].strip()
                    next_match = None
                    for next_m in group_matches:
                        if next_m.start() > match.start():
                            next_match = next_m
                            break
                    
                    if next_match:
                        lesson_text = cell_text[start_pos:next_match.start()].strip()
                    
                    if lesson_text and lesson_text not in lessons:
                        lessons.append(lesson_text)
                
                if not lessons:
                    return [cell_text]
                return lessons
            else:
                return [cell_text]
    
    def parse_lesson(self, cell_text):
        text = self.remove_all_groups(cell_text)
        
        lesson_type = ""
        type_match = TYPE_PATTERN.search(cell_text)
        if type_match:
            type_code = type_match.group(1)
            lesson_type = LESSON_TYPES.get(type_code, "")
            text = re.sub(r'\b' + re.escape(type_code) + r'\b', '', text)
        
        room = ""
        room_match = ROOM_PATTERN.search(text)
        if room_match:
            room = room_match.group()
            text = text.replace(room, "")
        
        teacher = ""
        teacher_match = TEACHER_PATTERN.search(text)
        if teacher_match:
            last_name = teacher_match.group(1)
            initials = teacher_match.group(2)
            teacher = f"{last_name} {initials}"
            text = text.replace(teacher_match.group(), "")
        else:
            simple_match = re.search(r'([А-Я][а-я]+)(?:\s|$)', text)
            if simple_match and len(simple_match.group(1)) > 2:
                teacher = simple_match.group(1)
                text = text.replace(teacher, "")
        
        group = self.extract_group_from_cell(cell_text)
        subject = re.sub(r'\s+', ' ', text).strip()
        
        return {
            'subject': subject,
            'teacher': teacher,
            'room': room,
            'type': lesson_type,
            'group': group,
            'full_text': cell_text
        }
    
    def extract_group_from_cell(self, cell_text):
        match = GROUP_PATTERN.search(cell_text)
        if match:
            return match.group(0)
        return ""
    
    async def load_fulltime_data(self):
        try:
            current_url = await self.get_latest_excel(SCHEDULE_PAGE)
            if not current_url:
                return False, "Не найдена ссылка для очников"
            
            file_content = await self.download_excel(current_url)
            if not file_content:
                return False, "Не удалось скачать файл"
            
            xl = pd.ExcelFile(io.BytesIO(file_content))
            groups_found = set()
            base_groups_found = set()
            subgroups_found = {}
            group_positions = {}
            dfs_cache = {}
            teacher_lessons = []
            room_lessons = []
            
            sheet_name = "Расписание (группы)"
            if sheet_name not in xl.sheet_names:
                sheet_name = xl.sheet_names[0]
                print(f"⚠️ Лист 'Расписание (группы)' не найден, используем: {sheet_name}")
            
            df = pd.read_excel(xl, sheet_name=sheet_name, header=None, dtype=str)
            dfs_cache[sheet_name] = df
            
            print(f"\n🔍 ПОИСК ЗАГОЛОВКОВ ГРУПП")
            
            for col_idx in range(len(df.columns)):
                cell = df.iat[0, col_idx]
                if pd.notna(cell):
                    cell_str = str(cell).strip()
                    if len(cell_str) > 1:
                        base_group, subgroup = self.parse_group_name(cell_str)
                        if base_group:
                            full_name = cell_str
                            groups_found.add(full_name)
                            base_groups_found.add(base_group)
                            
                            if full_name not in group_positions:
                                group_positions[full_name] = []
                            group_positions[full_name].append((sheet_name, 0, col_idx))
                            
                            if subgroup:
                                if base_group not in subgroups_found:
                                    subgroups_found[base_group] = set()
                                subgroups_found[base_group].add(subgroup)
                            
                            print(f"✅ Найдена группа: {full_name}")
            
            for week_start, week_end in [(2, 43), (44, 85)]:
                for col_idx in range(len(df.columns)):
                    for row_idx in range(week_start - 1, min(week_end, len(df))):
                        cell_val = str(df.iat[row_idx, col_idx]).strip() if pd.notna(df.iat[row_idx, col_idx]) else ""
                        if cell_val and cell_val not in ["", "nan", "—"]:
                            lessons_in_cell = self.split_lessons_in_cell(cell_val)
                            
                            for lesson_text in lessons_in_cell:
                                teacher_match = TEACHER_PATTERN.search(lesson_text)
                                if teacher_match:
                                    teacher_lessons.append({
                                        'cell': lesson_text,
                                        'row': row_idx + 1,
                                        'col': col_idx,
                                        'teacher': teacher_match.group(1)
                                    })
                                else:
                                    simple_match = re.search(r'([А-Я][а-я]+)(?:\s|$)', lesson_text)
                                    if simple_match and len(simple_match.group(1)) > 2:
                                        teacher_lessons.append({
                                            'cell': lesson_text,
                                            'row': row_idx + 1,
                                            'col': col_idx,
                                            'teacher': simple_match.group(1)
                                        })
                                
                                room_match = ROOM_PATTERN.search(lesson_text)
                                if room_match:
                                    room_lessons.append({
                                        'cell': lesson_text,
                                        'row': row_idx + 1,
                                        'col': col_idx,
                                        'room': room_match.group()
                                    })
            
            self.fulltime_groups = sorted(list(groups_found))
            self.fulltime_base_groups = sorted(list(base_groups_found))
            self.fulltime_subgroups = {k: sorted(list(v)) for k, v in subgroups_found.items()}
            self.fulltime_positions = group_positions
            self.fulltime_dfs = dfs_cache
            self.teacher_lessons = teacher_lessons
            self.room_lessons = room_lessons
            
            print(f"\n✅ Загружено {len(self.fulltime_base_groups)} базовых групп")
            print(f"👥 Найдено записей о преподавателях: {len(self.teacher_lessons)}")
            
            return True, f"✅ Очники: {len(self.fulltime_base_groups)} групп"
            
        except Exception as e:
            print(f"❌ Ошибка загрузки: {str(e)}")
            import traceback
            traceback.print_exc()
            return False, f"❌ Ошибка: {str(e)}"
    
    async def load_correspondence_data(self):
        try:
            corr_url, _ = await self.find_correspondence_links()
            if not corr_url:
                corr_url = FALLBACK_CORRESPONDENCE_URL
                print(f"⚠️ Использую запасную ссылку: {corr_url}")
            
            file_content = await self.download_excel(corr_url)
            if not file_content:
                return False, "Не удалось скачать файл заочников"
            
            xl = pd.ExcelFile(io.BytesIO(file_content))
            groups_found = set()
            group_positions = {}
            dfs_cache = {}
            lessons_by_group = {}
            
            sheet_name = xl.sheet_names[0]
            df = pd.read_excel(xl, sheet_name=sheet_name, header=None, dtype=str)
            dfs_cache[sheet_name] = df
            
            print(f"\n🔍 ПОИСК ЗАГОЛОВКОВ ГРУПП ЗАОЧНИКОВ")
            print(f"📊 Размер файла: {len(df)} строк, {len(df.columns)} столбцов")
            
            group_columns = {}
            for col_idx in range(len(df.columns)):
                cell = df.iat[0, col_idx]
                if pd.notna(cell):
                    cell_str = str(cell).strip()
                    if re.search(r'[З3][А-Я]{2,4}-\d{2,3}', cell_str) or re.search(r'[З3][А-Я]{2,3}-\d{2,3}', cell_str):
                        group_name = cell_str.strip()
                        groups_found.add(group_name)
                        group_columns[group_name] = col_idx
                        group_positions[group_name] = (sheet_name, 0, col_idx)
                        print(f"✅ Найдена группа: {group_name}")
            
            if not groups_found:
                print("⚠️ Не найдено групп в первой строке, ищем по всему файлу...")
                for row_idx in range(min(5, len(df))):
                    for col_idx in range(len(df.columns)):
                        cell = df.iat[row_idx, col_idx]
                        if pd.notna(cell):
                            cell_str = str(cell).strip()
                            if re.search(r'[З3][А-Я]{2,4}-\d{2,3}', cell_str) or re.search(r'[З3][А-Я]{2,3}-\d{2,3}', cell_str):
                                group_name = cell_str.strip()
                                if group_name not in groups_found:
                                    groups_found.add(group_name)
                                    group_columns[group_name] = col_idx
                                    group_positions[group_name] = (sheet_name, row_idx, col_idx)
                                    print(f"✅ Найдена группа в строке {row_idx}: {group_name}")
            
            print(f"\n📋 Найдено групп заочников: {len(groups_found)}")
            for g in groups_found:
                print(f"   - {g}")
            
            for group_name, col_idx in group_columns.items():
                lessons_by_group[group_name] = {}
                
                for row_idx in range(1, len(df)):
                    date_val = str(df.iat[row_idx, 0]).strip() if pd.notna(df.iat[row_idx, 0]) else ""
                    lesson_info = str(df.iat[row_idx, 1]).strip() if pd.notna(df.iat[row_idx, 1]) else ""
                    
                    lesson_num = 0
                    match = re.search(r'(\d+)\s*пара', lesson_info)
                    if match:
                        lesson_num = int(match.group(1))
                    
                    if lesson_num == 0:
                        continue
                    
                    day_name = ""
                    for day in ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]:
                        if day in lesson_info:
                            day_name = day
                            break
                    
                    cell_val = str(df.iat[row_idx, col_idx]).strip() if pd.notna(df.iat[row_idx, col_idx]) else ""
                    
                    if cell_val and cell_val not in ["", "nan", "—"]:
                        lessons_in_cell = self.split_lessons_in_cell(cell_val)
                        for lesson_text in lessons_in_cell:
                            lesson_data = self.parse_lesson(lesson_text)
                            lesson_data['date'] = date_val
                            lesson_data['day_name'] = day_name
                            lessons_by_group[group_name][lesson_num] = lesson_data
            
            self.correspondence_groups = sorted(list(groups_found))
            self.correspondence_positions = group_positions
            self.correspondence_dfs = dfs_cache
            self.correspondence_lessons = lessons_by_group
            
            print(f"\n✅ Заочники: загружено {len(self.correspondence_groups)} групп")
            
            return True, f"✅ Заочники: {len(self.correspondence_groups)} групп"
            
        except Exception as e:
            print(f"❌ Ошибка загрузки заочников: {str(e)}")
            import traceback
            traceback.print_exc()
            return False, f"❌ Ошибка: {str(e)}"
    
    async def load_consultations(self):
        """Загрузка консультаций из файла"""
        try:
            _, cons_url = await self.find_correspondence_links()
            print(f"🔍 Найден URL консультаций: {cons_url}")
            
            if not cons_url:
                cons_url = FALLBACK_CONSULTATIONS_URL
                print(f"⚠️ Использую запасную ссылку: {cons_url}")
            
            print(f"📥 Скачиваю файл: {cons_url}")
            file_content = await self.download_excel(cons_url)
            
            if not file_content:
                print("❌ Не удалось скачать файл консультаций")
                return
            
            print(f"✅ Файл скачан, размер: {len(file_content)} байт")
            
            xl = pd.ExcelFile(io.BytesIO(file_content))
            print(f"📊 Листы в файле: {xl.sheet_names}")
            
            # Используем первый лист "Консультации (группы)"
            df = pd.read_excel(xl, sheet_name=0, header=None, dtype=str)
            
            consultations_by_group = {}
            
            print(f"\n🔍 ЗАГРУЗКА КОНСУЛЬТАЦИЙ")
            print(f"📊 Размер файла: {len(df)} строк, {len(df.columns)} столбцов")
            
            # Показываем первые строки для отладки
            print("\n📋 Содержимое файла консультаций:")
            for i in range(min(20, len(df))):
                row_str = f"Строка {i}: "
                for j in range(min(len(df.columns), 8)):
                    val = df.iat[i, j] if pd.notna(df.iat[i, j]) else ""
                    if val:
                        row_str += f" | [{j}]{str(val)[:50]}"
                print(row_str)
            
            # Паттерны для поиска
            group_pattern = re.compile(r'([З3][А-Я]{2,3}-\d{2,3})', re.IGNORECASE)
            # Паттерн для даты в формате YYYY-MM-DD
            date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2})')
            time_pattern = re.compile(r'(\d{1,2}:\d{2})')
            
            current_date = None
            processed_times = set()  # Для отслеживания уже обработанных времен
            
            for row_idx in range(len(df)):
                # Читаем первый и второй столбцы
                first_cell = str(df.iat[row_idx, 0]) if pd.notna(df.iat[row_idx, 0]) else ""
                second_cell = str(df.iat[row_idx, 1]) if pd.notna(df.iat[row_idx, 1]) else ""
                
                # Проверяем, является ли первый столбец датой (формат YYYY-MM-DD)
                date_match = date_pattern.search(first_cell)
                if date_match:
                    current_date = date_match.group(1)
                    # Преобразуем в формат DD.MM.YYYY
                    date_parts = current_date.split('-')
                    if len(date_parts) == 3:
                        current_date = f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}"
                    print(f"📅 Строка {row_idx}: Найдена дата: {current_date}")
                    processed_times.clear()  # Сбрасываем обработанные времена для новой даты
                    continue
                
                # Если нет даты, пропускаем строку
                if not current_date:
                    continue
                
                # Получаем время из второго столбца
                current_time = None
                if second_cell and second_cell != 'nan':
                    time_match = time_pattern.search(second_cell)
                    if time_match:
                        current_time = time_match.group(1)
                        print(f"⏰ Строка {row_idx}: Найдено время: {current_time}, дата: {current_date}")
                    else:
                        # Если во втором столбце нет времени, пропускаем строку
                        continue
                
                if not current_time:
                    continue
                
                # Проверяем, не обрабатывали ли уже это время для текущей даты
                time_key = f"{current_date}_{current_time}"
                if time_key in processed_times:
                    print(f"⚠️ Время {current_time} для даты {current_date} уже обработано, пропускаем")
                    continue
                processed_times.add(time_key)
                
                # Обрабатываем столбцы с группами (начиная со 2-го)
                for col_idx in range(2, len(df.columns)):
                    cell_value = str(df.iat[row_idx, col_idx]) if pd.notna(df.iat[row_idx, col_idx]) else ""
                    
                    if not cell_value or cell_value == 'nan' or cell_value == 'None':
                        continue
                    
                    # Ищем группу в ячейке
                    group_match = group_pattern.search(cell_value)
                    if group_match:
                        group_name = group_match.group(1).upper()
                        print(f"   ✅ Найдена группа: {group_name} в строке {row_idx}, колонке {col_idx}")
                        
                        # Парсим содержимое ячейки
                        consultation = self.parse_consultation_cell(cell_value, current_date, current_time)
                        
                        if consultation and (consultation['subject'] or consultation['teacher']):
                            if group_name not in consultations_by_group:
                                consultations_by_group[group_name] = []
                            # Проверяем, нет ли уже такой консультации (по времени и предмету)
                            existing = False
                            for existing_cons in consultations_by_group[group_name]:
                                if existing_cons['time'] == current_time and existing_cons['subject'] == consultation['subject']:
                                    existing = True
                                    break
                            if not existing:
                                consultations_by_group[group_name].append(consultation)
                                print(f"   ✅ Добавлена консультация для {group_name}: {current_date} {current_time}")
                    else:
                        # Проверяем на наличие групп без паттерна
                        for test_group in ['ЗВС-22', 'ЗВС-23', 'ЗВС-24', 'ЗВС-25', 'ЗИС-22', 'ЗИС-23', 'ЗКС-22', 'ЗПС-22']:
                            if test_group in cell_value:
                                print(f"   ⚠️ Найдена группа {test_group} в строке {row_idx}, колонке {col_idx}")
                                group_name = test_group
                                consultation = self.parse_consultation_cell(cell_value, current_date, current_time)
                                if consultation and (consultation['subject'] or consultation['teacher']):
                                    if group_name not in consultations_by_group:
                                        consultations_by_group[group_name] = []
                                    # Проверяем, нет ли уже такой консультации
                                    existing = False
                                    for existing_cons in consultations_by_group[group_name]:
                                        if existing_cons['time'] == current_time and existing_cons['subject'] == consultation['subject']:
                                            existing = True
                                            break
                                    if not existing:
                                        consultations_by_group[group_name].append(consultation)
                                        print(f"   ✅ Добавлена консультация для {group_name} (ручное определение)")
                                break
            
            self.consultations = consultations_by_group
            print(f"\n✅ Загружено консультаций для {len(self.consultations)} групп")
            for group, cons in self.consultations.items():
                print(f"   - {group}: {len(cons)} консультаций")
                for c in cons[:5]:
                    print(f"      • {c['date']} {c['time']} - {c['subject'][:50] if c['subject'] else c['teacher']}")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки консультаций: {e}")
            import traceback
            traceback.print_exc()
    
    def parse_consultation_cell(self, cell_text, default_date=None, default_time=None):
        """Парсит ячейку с консультацией"""
        if not cell_text:
            return None
        
        consultation = {
            'date': default_date or "",
            'time': default_time or "",
            'subject': "",
            'teacher': "",
            'room': ""
        }
        
        # Убираем название группы из начала
        text = re.sub(r'^[З3][А-Я]{2,3}-\d{2,3}\s*', '', cell_text)
        
        # Ищем слово "Консультация"
        if 'Консультация' in text or 'консультация' in text.lower():
            # Разделяем по слову "Консультация"
            parts = re.split(r'Консультация', text, flags=re.IGNORECASE)
            if len(parts) >= 2:
                subject_part = parts[0].strip()
                teacher_room_part = parts[1].strip() if len(parts) > 1 else ""
                
                consultation['subject'] = subject_part
                
                # Ищем преподавателя (Фамилия И.О.)
                teacher_match = re.search(r'([А-Я][а-я]+)\s+([А-Я]\.[А-Я]\.?)', teacher_room_part)
                if teacher_match:
                    consultation['teacher'] = f"{teacher_match.group(1)} {teacher_match.group(2)}"
                    teacher_room_part = teacher_room_part.replace(teacher_match.group(0), '').strip()
                
                # Ищем аудиторию
                room_match = re.search(r'[А-Я]-\d{3}|[А-Я]-\d{2,3}|\b\d{3}\b', teacher_room_part)
                if room_match:
                    consultation['room'] = room_match.group()
        else:
            consultation['subject'] = text
            
            # Ищем преподавателя
            teacher_match = re.search(r'([А-Я][а-я]+)\s+([А-Я]\.[А-Я]\.?)', text)
            if teacher_match:
                consultation['teacher'] = f"{teacher_match.group(1)} {teacher_match.group(2)}"
            
            # Ищем аудиторию
            room_match = re.search(r'[А-Я]-\d{3}|[А-Я]-\d{2,3}|\b\d{3}\b', text)
            if room_match:
                consultation['room'] = room_match.group()
        
        # Очищаем от лишних символов
        consultation['subject'] = re.sub(r'\s+', ' ', consultation['subject']).strip()
        consultation['teacher'] = re.sub(r'\s+', ' ', consultation['teacher']).strip()
        consultation['room'] = consultation['room'].strip() if consultation['room'] else ""
        
        return consultation
    
    async def load_exam_sessions(self):
        """Загрузка расписания экзаменов для заочников"""
        try:
            corr_url, _ = await self.find_correspondence_links()
            if not corr_url:
                corr_url = FALLBACK_CORRESPONDENCE_URL
                print(f"⚠️ Использую запасную ссылку для экзаменов: {corr_url}")
            
            file_content = await self.download_excel(corr_url)
            if not file_content:
                print("Не удалось скачать файл экзаменов")
                return
            
            xl = pd.ExcelFile(io.BytesIO(file_content))
            df = pd.read_excel(xl, sheet_name=0, dtype=str)
            
            exams_by_group = {}
            
            headers = df.iloc[0].astype(str).tolist()
            
            group_col = None
            date_col = None
            time_col = None
            subject_col = None
            teacher_col = None
            room_col = None
            
            for i, header in enumerate(headers):
                header_lower = str(header).lower()
                if 'групп' in header_lower:
                    group_col = i
                elif 'дат' in header_lower:
                    date_col = i
                elif 'врем' in header_lower or 'час' in header_lower:
                    time_col = i
                elif 'предмет' in header_lower or 'дисциплин' in header_lower:
                    subject_col = i
                elif 'преподавател' in header_lower:
                    teacher_col = i
                elif 'аудитор' in header_lower or 'кабинет' in header_lower:
                    room_col = i
            
            if group_col is None:
                group_col = 0
            if date_col is None:
                date_col = 1
            if time_col is None:
                time_col = 2
            if subject_col is None:
                subject_col = 3
            if teacher_col is None:
                teacher_col = 4
            if room_col is None:
                room_col = 5
            
            for _, row in df.iloc[1:].iterrows():
                group = str(row.iloc[group_col]).strip().upper() if pd.notna(row.iloc[group_col]) else ""
                if not group or group == 'nan':
                    continue
                
                exam = {
                    'date': str(row.iloc[date_col]).strip() if pd.notna(row.iloc[date_col]) else "",
                    'time': str(row.iloc[time_col]).strip() if pd.notna(row.iloc[time_col]) else "",
                    'subject': str(row.iloc[subject_col]).strip() if pd.notna(row.iloc[subject_col]) else "",
                    'teacher': str(row.iloc[teacher_col]).strip() if pd.notna(row.iloc[teacher_col]) else "",
                    'room': str(row.iloc[room_col]).strip() if pd.notna(row.iloc[room_col]) else ""
                }
                
                for key in exam:
                    if exam[key] == 'nan':
                        exam[key] = ""
                
                if exam['subject']:
                    if group not in exams_by_group:
                        exams_by_group[group] = []
                    exams_by_group[group].append(exam)
            
            self.exam_sessions = exams_by_group
            print(f"✅ Загружено экзаменов для {len(self.exam_sessions)} групп")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки экзаменов: {e}")
    
    async def load_all_data(self):
        results = []
        fulltime_result = await self.load_fulltime_data()
        results.append(fulltime_result)
        correspondence_result = await self.load_correspondence_data()
        results.append(correspondence_result)
        await self.load_consultations()
        await self.load_exam_sessions()
        self.last_update = datetime.now()
        return results
    
    def find_group(self, group_name):
        group_upper = self.normalize_group_name(group_name)
        
        if self.is_correspondence_group(group_upper):
            for corr_group in self.correspondence_groups:
                if group_upper in corr_group or corr_group in group_upper:
                    return corr_group, "correspondence"
                if group_upper.replace('-', '') == corr_group.replace('-', ''):
                    return corr_group, "correspondence"
            return group_upper, "correspondence"
        
        if group_upper in self.fulltime_base_groups:
            return group_upper, "fulltime"
        
        for base_group in self.fulltime_base_groups:
            if group_upper in base_group or base_group in group_upper:
                return base_group, "fulltime"
        
        return None, None
    
    def get_subgroups(self, base_group):
        return self.fulltime_subgroups.get(base_group, [])
    
    def get_fulltime_column(self, base_group, subgroup):
        if subgroup:
            variants = [f"{base_group}-{subgroup}", f"{base_group}/{subgroup}"]
            for variant in variants:
                if variant in self.fulltime_positions:
                    for pos in self.fulltime_positions[variant]:
                        sheet, row, col = pos
                        return col, sheet
        
        if base_group in self.fulltime_positions:
            for pos in self.fulltime_positions[base_group]:
                sheet, row, col = pos
                return col, sheet
        
        return None, None
    
    def get_day_schedule_fulltime(self, base_group, subgroup, day_name, week_offset=0):
        week_display, week_start, week_end = get_week_type(week_offset)
        
        col, sheet = self.get_fulltime_column(base_group, subgroup)
        if col is None:
            return None
        
        day_start_excel = get_day_start_row(week_start, day_name)
        day_start_index = day_start_excel - 1
        
        df = self.fulltime_dfs[sheet]
        
        if subgroup:
            display_name = f"{base_group}-{subgroup}"
        else:
            display_name = base_group
        
        lessons = []
        seen_lessons = set()
        
        for i in range(7):
            row_index = day_start_index + i
            if row_index >= len(df):
                continue
            
            row_excel = row_index + 1
            if row_excel > week_end:
                continue
            
            cell_val = str(df.iat[row_index, col]).strip() if pd.notna(df.iat[row_index, col]) else ""
            
            if cell_val and cell_val not in ["", "nan", "—"]:
                lesson_num = i + 1
                lessons_in_cell = self.split_lessons_in_cell(cell_val)
                
                for lesson_text in lessons_in_cell:
                    lesson_data = self.parse_lesson(lesson_text)
                    
                    if subgroup:
                        lesson_group = lesson_data.get('group', '')
                        if lesson_group and str(subgroup) not in lesson_group:
                            continue
                    
                    lesson_key = f"{lesson_num}_{lesson_data['subject']}_{lesson_data['teacher']}"
                    
                    if lesson_key not in seen_lessons:
                        seen_lessons.add(lesson_key)
                        lessons.append({
                            'num': lesson_num,
                            'data': lesson_data
                        })
        
        if not lessons:
            return None
        
        return self.format_schedule(lessons, display_name, day_name, week_display, week_offset)
    
    def get_day_schedule_correspondence(self, group_name, day_name=None):
        if group_name not in self.correspondence_lessons:
            return None
        
        lessons_data = self.correspondence_lessons[group_name]
        if not lessons_data:
            return None
        
        week_display, _, _ = get_week_type()
        
        lessons = []
        seen_lessons = set()
        
        for lesson_num, lesson_data in lessons_data.items():
            if day_name and lesson_data.get('day_name') != day_name:
                continue
            
            lesson_key = f"{lesson_num}_{lesson_data['subject']}_{lesson_data['teacher']}"
            if lesson_key not in seen_lessons:
                seen_lessons.add(lesson_key)
                lessons.append({
                    'num': lesson_num,
                    'data': lesson_data
                })
        
        if not lessons:
            return None
        
        lessons.sort(key=lambda x: x['num'])
        
        if day_name:
            return self.format_schedule(lessons, group_name, day_name, week_display, show_date=True)
        else:
            return self.format_correspondence_schedule(lessons, group_name, week_display)
    
    def format_correspondence_schedule(self, lessons, group_name, week_display):
        result = []
        result.append("📚 <b>РАСПИСАНИЕ УСТАНОВОЧНОЙ СЕССИИ</b>")
        result.append(f"👥 Группа: {group_name}")
        result.append(week_display)
        result.append("—" * 40)
        
        current_date = ""
        for lesson in lessons:
            data = lesson['data']
            time = LESSON_TIMES.get(lesson['num'], "")
            date_str = data.get('date', '')
            day_name = data.get('day_name', '')
            
            if date_str != current_date:
                current_date = date_str
                result.append(f"\n📅 <b>{date_str} ({day_name})</b>")
            
            result.append(f"\n<b>{lesson['num']} пара</b> {time}")
            
            if data['subject']:
                result.append(f"📖 {data['subject']}")
            if data['teacher']:
                result.append(f"👤 {data['teacher']}")
            if data['room']:
                if re.match(r'^\d{3}$', data['room']):
                    result.append(f"🏫 ауд. {data['room']}")
                else:
                    result.append(f"🏫 {data['room']}")
            if data['type']:
                result.append(f"📌 {data['type']}")
            if data.get('group'):
                result.append(f"👥 {data['group']}")
            
            result.append("—" * 30)
        
        return "\n".join(result)
    
    def format_schedule(self, lessons, group_name, day_name, week_display, week_offset=0, show_date=False):
        date_str = get_date_for_day(day_name, week_offset) if not show_date else ""
        
        result = []
        result.append("📚 <b>РАСПИСАНИЕ</b>")
        result.append(f"👥 Группа: {group_name}")
        result.append(f"📅 {day_name} {date_str}")
        result.append(week_display)
        result.append("—" * 40)
        
        for lesson in lessons:
            data = lesson['data']
            time = LESSON_TIMES.get(lesson['num'], "")
            
            result.append(f"\n<b>{lesson['num']} пара</b> {time}")
            
            if data['subject']:
                result.append(f"📖 {data['subject']}")
            if data['teacher']:
                result.append(f"👤 {data['teacher']}")
            if data['room']:
                if re.match(r'^\d{3}$', data['room']):
                    result.append(f"🏫 ауд. {data['room']}")
                else:
                    result.append(f"🏫 {data['room']}")
            if data['type']:
                result.append(f"📌 {data['type']}")
            if data.get('group'):
                result.append(f"👥 {data['group']}")
            
            result.append("—" * 30)
        
        return "\n".join(result)
    
    def get_consultations_for_group(self, group_name):
        """Получение всех консультаций для группы (без дубликатов)"""
        search_name = group_name.upper().replace(' ', '').replace('-', '')
        
        print(f"🔍 Поиск всех консультаций: группа={search_name}")
        print(f"📋 Доступные группы: {list(self.consultations.keys())}")
        
        for group_key, cons in self.consultations.items():
            group_key_clean = group_key.upper().replace(' ', '').replace('-', '')
            if search_name == group_key_clean or search_name in group_key_clean or group_key_clean in search_name:
                print(f"✅ Найдена группа {group_key}")
                if not cons:
                    return None
                
                # Удаляем дубликаты по комбинации дата+время+предмет
                unique_cons = {}
                for c in cons:
                    key = f"{c.get('date', '')}_{c.get('time', '')}_{c.get('subject', '')}"
                    if key not in unique_cons:
                        unique_cons[key] = c
                
                sorted_cons = sorted(unique_cons.values(), key=lambda x: (x.get('date', ''), x.get('time', '')))
                
                result = []
                result.append("📚 <b>ВСЕ КОНСУЛЬТАЦИИ</b>")
                result.append(f"👥 Группа: {group_key}")
                result.append("—" * 40)
                
                current_date = ""
                for c in sorted_cons:
                    if c.get('date') and c['date'] != current_date:
                        current_date = c['date']
                        try:
                            date_obj = datetime.strptime(current_date, "%d.%m.%Y")
                            day_name = RUSSIAN_DAYS[date_obj.weekday()]
                            result.append(f"\n📅 <b>{current_date} ({day_name})</b>")
                        except:
                            result.append(f"\n📅 <b>{current_date}</b>")
                    
                    cons_line = ""
                    if c.get('time'):
                        cons_line += f"⏰ {c['time']}  "
                    if c.get('subject'):
                        cons_line += f"📖 {c['subject']}  "
                    if c.get('teacher'):
                        cons_line += f"👤 {c['teacher']}  "
                    if c.get('room'):
                        cons_line += f"🏫 {c['room']}"
                    
                    if cons_line.strip():
                        result.append(cons_line)
                    result.append("—" * 30)
                
                return "\n".join(result)
        
        print(f"❌ Группа {search_name} не найдена")
        return None
    
    def get_consultations_for_date(self, group_name, target_date=None):
        """Получение консультаций для группы на конкретную дату (без дубликатов)"""
        if target_date is None:
            target_date = datetime.now().strftime("%d.%m.%Y")
        
        target_date_normalized = target_date.replace('/', '.')
        search_name = group_name.upper().replace(' ', '').replace('-', '')
        
        print(f"🔍 Поиск консультаций: группа={search_name}, дата={target_date_normalized}")
        print(f"📋 Доступные группы: {list(self.consultations.keys())}")
        
        for group_key, cons in self.consultations.items():
            group_key_clean = group_key.upper().replace(' ', '').replace('-', '')
            if search_name == group_key_clean or search_name in group_key_clean or group_key_clean in search_name:
                print(f"   ✅ Группа найдена: {group_key}")
                if not cons:
                    print(f"   ⚠️ Нет консультаций для группы")
                    return None
                
                # Удаляем дубликаты по комбинации дата+время+предмет
                unique_cons = {}
                for c in cons:
                    key = f"{c.get('date', '')}_{c.get('time', '')}_{c.get('subject', '')}"
                    if key not in unique_cons:
                        unique_cons[key] = c
                
                # Фильтруем по дате
                filtered_cons = []
                for c in unique_cons.values():
                    cons_date = c.get('date', '')
                    cons_date_normalized = cons_date.replace('/', '.')
                    if cons_date_normalized == target_date_normalized:
                        filtered_cons.append(c)
                        print(f"      ✅ Совпадает!")
                
                if not filtered_cons:
                    print(f"   ⚠️ Нет консультаций на дату {target_date_normalized}")
                    return None
                
                sorted_cons = sorted(filtered_cons, key=lambda x: x.get('time', ''))
                
                try:
                    date_obj = datetime.strptime(target_date_normalized, "%d.%m.%Y")
                    day_name = RUSSIAN_DAYS[date_obj.weekday()]
                except:
                    day_name = ""
                
                result = []
                result.append("📚 <b>КОНСУЛЬТАЦИИ НА СЕГОДНЯ</b>")
                result.append(f"👥 Группа: {group_key}")
                result.append(f"📅 {target_date_normalized} ({day_name})")
                result.append("—" * 40)
                
                for c in sorted_cons:
                    cons_line = ""
                    if c.get('time'):
                        cons_line += f"⏰ {c['time']}  "
                    if c.get('subject'):
                        cons_line += f"📖 {c['subject']}  "
                    if c.get('teacher'):
                        cons_line += f"👤 {c['teacher']}  "
                    if c.get('room'):
                        cons_line += f"🏫 {c['room']}"
                    
                    if cons_line.strip():
                        result.append(cons_line)
                    result.append("—" * 30)
                
                return "\n".join(result)
        
        print(f"❌ Группа {search_name} не найдена в консультациях")
        return None
    
    def get_today_consultations(self, group_name):
        """Получение консультаций на сегодня"""
        today = datetime.now().strftime("%d.%m.%Y")
        print(f"🔍 get_today_consultations: {group_name}, today={today}")
        result = self.get_consultations_for_date(group_name, today)
        if result:
            print(f"✅ Найдены консультации для {group_name}")
        else:
            print(f"❌ Консультации для {group_name} не найдены")
        return result
    
    def get_exams_for_group(self, group_name):
        if group_name not in self.exam_sessions:
            return None
        
        exams = self.exam_sessions[group_name]
        if not exams:
            return None
        
        result = []
        result.append("📚 <b>РАСПИСАНИЕ ЭКЗАМЕНОВ (СЕССИЯ)</b>")
        result.append(f"👥 Группа: {group_name}")
        result.append("—" * 40)
        
        sorted_exams = sorted(exams, key=lambda x: x.get('date', ''))
        
        for exam in sorted_exams:
            result.append(f"\n📅 {exam.get('date', 'дата не указана')}")
            if exam.get('time'):
                result.append(f"⏰ {exam['time']}")
            if exam.get('subject'):
                result.append(f"📖 {exam['subject']}")
            if exam.get('teacher'):
                result.append(f"👤 {exam['teacher']}")
            if exam.get('room'):
                result.append(f"🏫 {exam['room']}")
            result.append("—" * 30)
        
        return "\n".join(result)
    
    def search_teacher(self, teacher_name, day_name=None, week_offset=0):
        results = []
        week_display, week_start, week_end = get_week_type(week_offset)
        
        if day_name:
            day_start = get_day_start_row(week_start, day_name)
            row_start = day_start - 1
            row_end = row_start + 6
        else:
            row_start = week_start - 1
            row_end = week_end - 1
        
        search_name = teacher_name.strip().upper()
        search_name = re.sub(r'\s+[А-Я]\.[А-Я]\.?', '', search_name).strip()
        
        lessons_by_num = {}
        
        for lesson in self.teacher_lessons:
            if row_start <= lesson['row'] - 1 <= row_end:
                teacher_last_name = lesson['teacher'].upper()
                
                if teacher_last_name == search_name:
                    lesson_num = (lesson['row'] - row_start - 1) % 7 + 1
                    lesson_data = self.parse_lesson(lesson['cell'])
                    lesson_key = f"{lesson_num}_{lesson_data['subject']}_{lesson_data['teacher']}_{lesson_data.get('group', '')}"
                    
                    if lesson_key not in lessons_by_num:
                        lessons_by_num[lesson_key] = {
                            'data': lesson_data,
                            'num': lesson_num,
                            'row': lesson['row']
                        }
        
        results = list(lessons_by_num.values())
        results.sort(key=lambda x: (x['num'], x['row']))
        return results
    
    def format_teacher_schedule(self, results, teacher_name, day_name, week_offset=0):
        if not results:
            return None
        
        week_display, _, _ = get_week_type(week_offset)
        date_str = get_date_for_day(day_name, week_offset)
        
        result_lines = []
        result_lines.append(f"👤 <b>ПРЕПОДАВАТЕЛЬ: {teacher_name}</b>")
        result_lines.append(f"📅 {day_name} {date_str}")
        result_lines.append(week_display)
        result_lines.append("—" * 40)
        
        lessons_by_num = {}
        for r in results:
            num = r['num']
            if num not in lessons_by_num:
                lessons_by_num[num] = []
            lessons_by_num[num].append(r)
        
        for lesson_num in sorted(lessons_by_num.keys()):
            time = LESSON_TIMES.get(lesson_num, "")
            result_lines.append(f"\n<b>{lesson_num} пара</b> {time}")
            
            seen_in_lesson = set()
            for r in lessons_by_num[lesson_num]:
                data = r['data']
                
                lesson_key = f"{data['subject']}_{data['teacher']}_{data.get('group', '')}"
                if lesson_key in seen_in_lesson:
                    continue
                seen_in_lesson.add(lesson_key)
                
                if data['subject']:
                    result_lines.append(f"📖 {data['subject']}")
                
                group = data.get('group', '')
                if not group:
                    group = self.extract_group_from_cell(data['full_text'])
                if group:
                    result_lines.append(f"👥 {group}")
                
                if data['room']:
                    if re.match(r'^\d{3}$', data['room']):
                        result_lines.append(f"🏫 ауд. {data['room']}")
                    else:
                        result_lines.append(f"🏫 {data['room']}")
                
                if data['type']:
                    result_lines.append(f"📌 {data['type']}")
                
                result_lines.append("—" * 30)
        
        return "\n".join(result_lines)

# --- КЛАВИАТУРЫ ---
def get_user_type_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👨‍🎓 Студент"), KeyboardButton(text="👨‍🏫 Преподаватель")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_main_keyboard_student():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Текущая неделя"), KeyboardButton(text="📅 Следующая неделя")],
            [KeyboardButton(text="📋 Список групп"), KeyboardButton(text="🔄 Обновить")],
            [KeyboardButton(text="🔄 Сменить пользователя")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_main_keyboard_correspondence():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Консультации сегодня"), KeyboardButton(text="📋 Все консультации")],
            [KeyboardButton(text="📅 Расписание сессии"), KeyboardButton(text="📋 Экзамены")],
            [KeyboardButton(text="🔄 Обновить"), KeyboardButton(text="🔄 Сменить пользователя")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_main_keyboard_teacher():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Текущая неделя"), KeyboardButton(text="📅 Следующая неделя")],
            [KeyboardButton(text="🔄 Обновить")],
            [KeyboardButton(text="🔄 Сменить пользователя")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_day_selection_keyboard(user_type, week_type):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Понедельник"), KeyboardButton(text="Вторник")],
            [KeyboardButton(text="Среда"), KeyboardButton(text="Четверг")],
            [KeyboardButton(text="Пятница"), KeyboardButton(text="Суббота")],
            [KeyboardButton(text="◀️ Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_subgroups_keyboard(subgroups):
    keyboard = []
    row = []
    
    for sg in subgroups:
        row.append(KeyboardButton(text=f"Подгруппа {sg}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([KeyboardButton(text="◀️ Назад")])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_back_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="◀️ Назад")]],
        resize_keyboard=True
    )
    return keyboard

# --- ОБРАБОТЧИКИ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
schedule = ScheduleMaster()

@dp.message(Command('start'))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id in user_data:
        user_type = user_data[user_id].get('type')
        group_type = user_data[user_id].get('group_type', '')
        
        if user_type == 'student':
            if group_type == 'correspondence':
                week_display, _, _ = get_week_type()
                await message.answer(
                    f"👋 С возвращением! Ваша группа: {user_data[user_id].get('display_name', '')}\n{week_display}",
                    reply_markup=get_main_keyboard_correspondence()
                )
            else:
                display = user_data[user_id].get('display_name', user_data[user_id].get('group', ''))
                week_display, _, _ = get_week_type()
                await message.answer(
                    f"👋 С возвращением! Ваша группа: {display}\n{week_display}",
                    reply_markup=get_main_keyboard_student()
                )
        else:
            teacher_name = user_data[user_id].get('teacher_name', '')
            week_display, _, _ = get_week_type()
            await message.answer(
                f"👋 С возвращением! Вы зарегистрированы как преподаватель: {teacher_name}\n{week_display}",
                reply_markup=get_main_keyboard_teacher()
            )
        return
    
    await message.answer(
        "👋 Добро пожаловать!\n\nВы студент или преподаватель?",
        reply_markup=get_user_type_keyboard()
    )
    await state.set_state(States.waiting_for_user_type)

@dp.message(States.waiting_for_user_type)
async def process_user_type(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if message.text == "👨‍🎓 Студент":
        await state.update_data(user_type='student')
        await message.answer(
            "Введите вашу группу:\n\n"
            "Для очников: ИВБ-24, ИВБ-25, ВРБ-21\n"
            "Для заочников: ЗВС-22, ЗВС-24 (начинается с буквы З)",
            reply_markup=get_back_keyboard()
        )
        await state.set_state(States.waiting_for_group)
    
    elif message.text == "👨‍🏫 Преподаватель":
        await state.update_data(user_type='teacher')
        await message.answer(
            "Введите вашу фамилию (например: Иванов, Петрова):",
            reply_markup=get_back_keyboard()
        )
        await state.set_state(States.waiting_for_teacher_name)
    
    else:
        await message.answer("Пожалуйста, выберите 'Студент' или 'Преподаватель'")

@dp.message(States.waiting_for_teacher_name)
async def process_teacher_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())
        return
    
    teacher_name = message.text.strip()
    user_id = message.from_user.id
    
    parts = teacher_name.split()
    
    if len(parts) >= 1:
        last_name = parts[0].capitalize()
        if len(parts) >= 2:
            teacher_name = f"{last_name} {parts[1]}"
        else:
            teacher_name = last_name
    
    user_data[user_id] = {
        'type': 'teacher',
        'teacher_name': teacher_name,
        'display_name': teacher_name
    }
    
    week_display, _, _ = get_week_type()
    
    await message.answer(
        f"✅ Вы зарегистрированы как преподаватель: {teacher_name}\n{week_display}\n\n"
        f"Теперь вы можете смотреть своё расписание.",
        reply_markup=get_main_keyboard_teacher()
    )
    await state.clear()

@dp.message(States.waiting_for_group)
async def process_group(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())
        return
    
    user_id = message.from_user.id
    group_name = message.text.strip().upper()
    
    msg = await message.answer("🔍 Ищу группу...")
    
    exact_group, group_type = schedule.find_group(group_name)
    
    if not exact_group:
        await msg.delete()
        await message.answer(
            f"❌ Группа '{group_name}' не найдена.\n\n"
            f"Нажмите '📋 Список групп' чтобы увидеть все группы.",
            reply_markup=get_main_keyboard_student()
        )
        await state.clear()
        return
    
    user_data[user_id] = {
        'type': 'student',
        'group': exact_group,
        'group_type': group_type,
        'display_name': exact_group
    }
    
    week_display, _, _ = get_week_type()
    await msg.delete()
    
    if group_type == "fulltime":
        subgroups = schedule.get_subgroups(exact_group)
        if subgroups:
            await state.update_data(base_group=exact_group, group_type=group_type)
            await message.answer(
                f"✅ Найдена группа {exact_group}\n\n"
                f"У этой группы есть подгруппы. Выберите свою:",
                reply_markup=get_subgroups_keyboard(subgroups)
            )
            await state.set_state(States.waiting_for_subgroup)
            return
        
        today = get_today_name()
        result = schedule.get_day_schedule_fulltime(exact_group, None, today)
        if result:
            await message.answer(result, parse_mode='HTML')
        else:
            await message.answer(f"📭 На сегодня ({today}) занятий нет")
        
        await message.answer(
            f"✅ Группа {exact_group} сохранена!\n{week_display}",
            reply_markup=get_main_keyboard_student()
        )
    else:
        # Заочник - показываем ТОЛЬКО консультации на сегодня при регистрации
        today_cons = schedule.get_today_consultations(exact_group)
        
        if today_cons:
            await message.answer(today_cons, parse_mode='HTML')
        else:
            await message.answer(f"📭 На сегодня консультаций для группы {exact_group} нет")
        
        # НЕ показываем все консультации автоматически при регистрации
        # Они будут доступны по кнопке "📋 Все консультации"
        
        # Показываем расписание сессии (если есть)
        result = schedule.get_day_schedule_correspondence(exact_group, None)
        if result:
            await message.answer(result, parse_mode='HTML')
        
        # Показываем экзамены (если есть)
        exams_result = schedule.get_exams_for_group(exact_group)
        if exams_result:
            await message.answer(exams_result, parse_mode='HTML')
        
        await message.answer(
            f"✅ Группа {exact_group} сохранена!\n{week_display}",
            reply_markup=get_main_keyboard_correspondence()
        )
    
    await state.clear()

@dp.message(States.waiting_for_subgroup)
async def process_subgroup(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())
        return
    
    user_id = message.from_user.id
    data = await state.get_data()
    base_group = data['base_group']
    group_type = data['group_type']
    
    if message.text.startswith("Подгруппа "):
        try:
            subgroup = int(message.text.replace("Подгруппа ", ""))
        except:
            await message.answer("Пожалуйста, выберите подгруппу из меню")
            return
    else:
        await message.answer("Пожалуйста, выберите подгруппу из меню")
        return
    
    display_name = f"{base_group}-{subgroup}"
    user_data[user_id] = {
        'type': 'student',
        'group': base_group,
        'subgroup': subgroup,
        'group_type': group_type,
        'display_name': display_name
    }
    
    week_display, _, _ = get_week_type()
    
    today = get_today_name()
    result = schedule.get_day_schedule_fulltime(base_group, subgroup, today)
    
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 На сегодня ({today}) занятий нет")
    
    await message.answer(
        f"✅ Группа {display_name} сохранена!\n{week_display}",
        reply_markup=get_main_keyboard_student()
    )
    await state.clear()

@dp.message(F.text == "📅 Текущая неделя")
async def current_week_menu(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    user_type = user_data[user_id].get('type')
    group_type = user_data[user_id].get('group_type', '')
    
    if group_type == 'correspondence':
        await message.answer("❌ Для заочников расписание по дням недели не предусмотрено.\nИспользуйте кнопки '📋 Консультации сегодня', '📋 Все консультации' и '📋 Экзамены'")
        return
    
    await state.update_data(week_offset=0)
    
    if user_type == 'student':
        group_info = user_data[user_id]
        if group_info.get('group_type') == "fulltime":
            await message.answer(
                "Выберите день (текущая неделя):",
                reply_markup=get_day_selection_keyboard('student', 'current')
            )
            await state.set_state(States.waiting_for_day)
    else:
        await message.answer(
            "Выберите день (текущая неделя):",
            reply_markup=get_day_selection_keyboard('teacher', 'current')
        )
        await state.set_state(States.waiting_for_teacher_day)

@dp.message(F.text == "📅 Следующая неделя")
async def next_week_menu(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    user_type = user_data[user_id].get('type')
    group_type = user_data[user_id].get('group_type', '')
    
    if group_type == 'correspondence':
        await message.answer("❌ Для заочников расписание по дням недели не предусмотрено.\nИспользуйте кнопки '📋 Консультации сегодня', '📋 Все консультации' и '📋 Экзамены'")
        return
    
    await state.update_data(week_offset=1)
    
    if user_type == 'student':
        group_info = user_data[user_id]
        if group_info.get('group_type') == "fulltime":
            await message.answer(
                "Выберите день (следующая неделя):",
                reply_markup=get_day_selection_keyboard('student', 'next')
            )
            await state.set_state(States.waiting_for_day)
    else:
        await message.answer(
            "Выберите день (следующая неделя):",
            reply_markup=get_day_selection_keyboard('teacher', 'next')
        )
        await state.set_state(States.waiting_for_teacher_day)

@dp.message(F.text == "📋 Консультации сегодня")
async def show_today_consultations(message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    group_info = user_data[user_id]
    if group_info.get('group_type') != 'correspondence':
        await message.answer("❌ Консультации доступны только для заочников")
        return
    
    group_name = group_info['group']
    today = datetime.now().strftime("%d.%m.%Y")
    today_name = get_today_name()
    
    result = schedule.get_consultations_for_date(group_name, today)
    
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 Для группы {group_name} на {today_name} ({today}) консультаций нет")

@dp.message(F.text == "📋 Все консультации")
async def show_all_consultations(message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    group_info = user_data[user_id]
    if group_info.get('group_type') != 'correspondence':
        await message.answer("❌ Консультации доступны только для заочников")
        return
    
    group_name = group_info['group']
    result = schedule.get_consultations_for_group(group_name)
    
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 Для группы {group_name} консультации не найдены")

@dp.message(F.text == "📋 Экзамены")
async def show_exams(message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    group_info = user_data[user_id]
    if group_info.get('group_type') != 'correspondence':
        await message.answer("❌ Экзамены доступны только для заочников")
        return
    
    group_name = group_info['group']
    result = schedule.get_exams_for_group(group_name)
    
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 Для группы {group_name} экзамены не найдены")

@dp.message(F.text == "📅 Расписание сессии")
async def show_session_schedule(message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    
    group_info = user_data[user_id]
    if group_info.get('group_type') != 'correspondence':
        await message.answer("❌ Расписание сессии доступно только для заочников")
        return
    
    group_name = group_info['group']
    result = schedule.get_day_schedule_correspondence(group_name, None)
    
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 Для группы {group_name} расписание сессии не найдено")

@dp.message(States.waiting_for_day)
async def show_group_schedule(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        user_type = user_data.get(message.from_user.id, {}).get('type', 'student')
        if user_type == 'teacher':
            await message.answer("Главное меню:", reply_markup=get_main_keyboard_teacher())
        else:
            await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())
        return
    
    user_id = message.from_user.id
    group_info = user_data[user_id]
    day_name = message.text
    data = await state.get_data()
    week_offset = data.get('week_offset', 0)
    
    if day_name not in DAYS:
        await message.answer("Пожалуйста, выберите день из меню")
        return
    
    msg = await message.answer(f"🔍 Загружаю расписание на {day_name}...")
    
    subgroup = group_info.get('subgroup')
    result = schedule.get_day_schedule_fulltime(group_info['group'], subgroup, day_name, week_offset)
    await msg.delete()
    
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        week_text = "следующей" if week_offset == 1 else "текущей"
        await message.answer(f"📭 На {day_name} {week_text} недели занятий нет")
    
    await message.answer(
        "Выберите другой день:",
        reply_markup=get_day_selection_keyboard('student', 'current' if week_offset == 0 else 'next')
    )

@dp.message(States.waiting_for_teacher_day)
async def show_teacher_schedule(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        user_type = user_data.get(message.from_user.id, {}).get('type', 'teacher')
        if user_type == 'teacher':
            await message.answer("Главное меню:", reply_markup=get_main_keyboard_teacher())
        else:
            await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())
        return
    
    user_id = message.from_user.id
    teacher_name = user_data[user_id].get('teacher_name', '')
    day_name = message.text
    data = await state.get_data()
    week_offset = data.get('week_offset', 0)
    
    if day_name not in DAYS:
        await message.answer("Пожалуйста, выберите день из меню")
        return
    
    msg = await message.answer(f"🔍 Загружаю расписание для преподавателя {teacher_name}...")
    
    results = schedule.search_teacher(teacher_name, day_name, week_offset)
    await msg.delete()
    
    if results:
        formatted = schedule.format_teacher_schedule(results, teacher_name, day_name, week_offset)
        await message.answer(formatted, parse_mode='HTML')
    else:
        week_text = "следующей" if week_offset == 1 else "текущей"
        await message.answer(f"📭 Для преподавателя {teacher_name} на {day_name} {week_text} недели занятий нет")
    
    await message.answer(
        "Выберите другой день:",
        reply_markup=get_day_selection_keyboard('teacher', 'current' if week_offset == 0 else 'next')
    )

@dp.message(F.text == "📋 Список групп")
async def show_groups(message: Message):
    all_groups = []
    
    for group in schedule.fulltime_base_groups:
        subgroups = schedule.get_subgroups(group)
        if subgroups:
            all_groups.append(f"{group} (очное, подгруппы: {', '.join(map(str, subgroups))})")
        else:
            all_groups.append(f"{group} (очное)")
    
    for group in schedule.correspondence_groups:
        all_groups.append(f"{group} (заочное)")
    
    if not all_groups:
        await message.answer("⏳ Группы не загружены. Нажмите '🔄 Обновить'")
        return
    
    text = "📚 <b>ВСЕ ГРУППЫ</b>\n\n"
    for group in all_groups:
        if len(text) + len(group) + 10 > 3500:
            await message.answer(text, parse_mode='HTML')
            text = "📚 <b>ПРОДОЛЖЕНИЕ</b>\n\n"
        text += f"• {group}\n"
    
    if text:
        await message.answer(text, parse_mode='HTML')
    
    user_type = user_data.get(message.from_user.id, {}).get('type', 'student')
    group_type = user_data.get(message.from_user.id, {}).get('group_type', '')
    
    if user_type == 'teacher':
        await message.answer(f"✅ Всего групп: {len(all_groups)}", reply_markup=get_main_keyboard_teacher())
    else:
        if group_type == 'correspondence':
            await message.answer(f"✅ Всего групп: {len(all_groups)}", reply_markup=get_main_keyboard_correspondence())
        else:
            await message.answer(f"✅ Всего групп: {len(all_groups)}", reply_markup=get_main_keyboard_student())

@dp.message(F.text == "🔄 Обновить")
async def update_data(message: Message):
    msg = await message.answer("🔄 Обновляю данные...")
    results = await schedule.load_all_data()
    await msg.delete()
    
    text = "📊 <b>РЕЗУЛЬТАТ</b>\n\n"
    for success, result in results:
        text += f"{result}\n"
    
    user_type = user_data.get(message.from_user.id, {}).get('type', 'student')
    group_type = user_data.get(message.from_user.id, {}).get('group_type', '')
    
    if user_type == 'teacher':
        await message.answer(text, parse_mode='HTML', reply_markup=get_main_keyboard_teacher())
    else:
        if group_type == 'correspondence':
            await message.answer(text, parse_mode='HTML', reply_markup=get_main_keyboard_correspondence())
        else:
            await message.answer(text, parse_mode='HTML', reply_markup=get_main_keyboard_student())

@dp.message(F.text == "🔄 Сменить пользователя")
async def change_user(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in user_data:
        del user_data[user_id]
    
    await state.clear()
    await message.answer(
        "👋 Вы сменили пользователя.\n\nВы студент или преподаватель?",
        reply_markup=get_user_type_keyboard()
    )
    await state.set_state(States.waiting_for_user_type)

@dp.message(F.text == "◀️ Назад")
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    user_type = user_data.get(message.from_user.id, {}).get('type', 'student')
    group_type = user_data.get(message.from_user.id, {}).get('group_type', '')
    
    if user_type == 'teacher':
        await message.answer("Главное меню:", reply_markup=get_main_keyboard_teacher())
    else:
        if group_type == 'correspondence':
            await message.answer("Главное меню:", reply_markup=get_main_keyboard_correspondence())
        else:
            await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())

# --- ЗАПУСК ---
async def main():
    print("🤖 Запуск бота...")
    await schedule.start()
    results = await schedule.load_all_data()
    for success, result in results:
        print(result)
    
    today = get_today_name()
    week_display, _, _ = get_week_type()
    print(f"📅 Сегодня: {today}")
    print(f"📅 Текущая неделя: {week_display}")
    print(f"✅ Бот готов!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())