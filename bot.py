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
from aiogram.client.bot import DefaultBotProperties

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
    waiting_for_reset = State()
    waiting_for_student_teacher_search = State()
    waiting_for_student_teacher_week = State()
    waiting_for_student_teacher_day = State()

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
        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=10)
        self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def stop(self):
        if self.session:
            await self.session.close()
    
    async def ensure_data_loaded(self):
        if self.last_update is None:
            print("📂 Первичная загрузка данных...")
            await self.load_all_data()
        else:
            print("✅ Данные из кэша")
    
    async def fetch_page(self, url):
        for attempt in range(3):
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                async with self.session.get(url, timeout=15, ssl=False, headers=headers) as response:
                    if response.status == 200:
                        return await response.text()
            except:
                await asyncio.sleep(1)
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
        return excel_links[0] if excel_links else None
    
    async def download_excel(self, url):
        for attempt in range(3):
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                async with self.session.get(url, timeout=30, ssl=False, headers=headers) as response:
                    if response.status == 200:
                        return await response.read()
            except:
                await asyncio.sleep(2)
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
            if ('консультац' in text or 'конс' in text) and ('.xlsx' in href or '.xls' in href):
                full_url = href if href.startswith('http') else f"{UNIVERSITY_URL}{href}"
                consultations_url = full_url
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
            df = pd.read_excel(xl, sheet_name=sheet_name, header=None, dtype=str)
            dfs_cache[sheet_name] = df
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
            return True, f"✅ Очники: {len(self.fulltime_base_groups)} групп"
        except Exception as e:
            return False, f"❌ Ошибка: {str(e)}"
    
    async def load_correspondence_data(self):
        try:
            corr_url, _ = await self.find_correspondence_links()
            if not corr_url:
                corr_url = FALLBACK_CORRESPONDENCE_URL
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
            return True, f"✅ Заочники: {len(self.correspondence_groups)} групп"
        except Exception as e:
            return False, f"❌ Ошибка: {str(e)}"
    
    async def load_consultations(self):
        try:
            _, cons_url = await self.find_correspondence_links()
            if not cons_url:
                cons_url = FALLBACK_CONSULTATIONS_URL
            file_content = await self.download_excel(cons_url)
            if not file_content:
                print("Не удалось скачать файл консультаций")
                return
            xl = pd.ExcelFile(io.BytesIO(file_content))
            df = pd.read_excel(xl, sheet_name=0, header=None, dtype=str)
            consultations_by_group = {}
            group_pattern = re.compile(r'([З3][А-Я]{2,3}-\d{2,3})', re.IGNORECASE)
            date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2})')
            time_pattern = re.compile(r'(\d{1,2}:\d{2})')
            current_date = None
            processed_times = set()
            for row_idx in range(len(df)):
                first_cell = str(df.iat[row_idx, 0]) if pd.notna(df.iat[row_idx, 0]) else ""
                second_cell = str(df.iat[row_idx, 1]) if pd.notna(df.iat[row_idx, 1]) else ""
                date_match = date_pattern.search(first_cell)
                if date_match:
                    current_date = date_match.group(1)
                    date_parts = current_date.split('-')
                    if len(date_parts) == 3:
                        current_date = f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}"
                    processed_times.clear()
                    continue
                if not current_date:
                    continue
                current_time = None
                if second_cell and second_cell != 'nan' and ':' in second_cell:
                    current_time = second_cell.strip()
                if not current_time:
                    continue
                time_key = f"{current_date}_{current_time}"
                if time_key in processed_times:
                    continue
                processed_times.add(time_key)
                for col_idx in range(2, len(df.columns)):
                    cell_value = str(df.iat[row_idx, col_idx]) if pd.notna(df.iat[row_idx, col_idx]) else ""
                    if not cell_value or cell_value == 'nan' or cell_value == 'None':
                        continue
                    group_match = group_pattern.search(cell_value)
                    if group_match:
                        group_name = group_match.group(1).upper()
                        consultation = {
                            'date': current_date,
                            'time': current_time,
                            'subject': cell_value[:50] if len(cell_value) > 50 else cell_value,
                            'teacher': "",
                            'room': ""
                        }
                        if group_name not in consultations_by_group:
                            consultations_by_group[group_name] = []
                        consultations_by_group[group_name].append(consultation)
            self.consultations = consultations_by_group
        except Exception as e:
            print(f"Ошибка загрузки консультаций: {e}")
    
    async def load_all_data(self):
        results = []
        fulltime_result = await self.load_fulltime_data()
        results.append(fulltime_result)
        correspondence_result = await self.load_correspondence_data()
        results.append(correspondence_result)
        await self.load_consultations()
        self.last_update = datetime.now()
        print("✅ Все данные загружены")
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
        if group_name not in self.consultations:
            return None
        cons = self.consultations[group_name]
        if not cons:
            return None
        result = []
        result.append("📚 <b>КОНСУЛЬТАЦИИ</b>")
        result.append(f"👥 Группа: {group_name}")
        result.append("—" * 40)
        for c in cons:
            result.append(f"\n📅 {c.get('date', 'дата не указана')}")
            if c.get('time'):
                result.append(f"⏰ {c['time']}")
            if c.get('subject'):
                result.append(f"📖 {c['subject']}")
            if c.get('teacher'):
                result.append(f"👤 {c['teacher']}")
            if c.get('room'):
                result.append(f"🏫 {c['room']}")
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
        return "\n".join(result)
    
    def format_student_teacher_search(self, results, teacher_name, day_name, week_offset=0):
        if not results:
            return None
        week_display, _, _ = get_week_type(week_offset)
        date_str = get_date_for_day(day_name, week_offset)
        result_lines = []
        result_lines.append(f"👨‍🏫 <b>ПРЕПОДАВАТЕЛЬ: {teacher_name}</b>")
        result_lines.append(f"📅 {day_name} {date_str}")
        result_lines.append(week_display)
        result_lines.append("=" * 40)
        lessons_by_num = {}
        for r in results:
            num = r['num']
            if num not in lessons_by_num:
                lessons_by_num[num] = []
            lessons_by_num[num].append(r)
        for lesson_num in sorted(lessons_by_num.keys()):
            time = LESSON_TIMES.get(lesson_num, "")
            result_lines.append(f"\n<b>⏰ {lesson_num} пара</b> ({time})")
            seen_in_lesson = set()
            for r in lessons_by_num[lesson_num]:
                data = r['data']
                lesson_key = f"{data['subject']}_{data.get('group', '')}_{data['room']}"
                if lesson_key in seen_in_lesson:
                    continue
                seen_in_lesson.add(lesson_key)
                if data['subject']:
                    result_lines.append(f"📖 <b>{data['subject']}</b>")
                group = data.get('group', '')
                if not group:
                    group = self.extract_group_from_cell(data['full_text'])
                if group:
                    result_lines.append(f"👥 <b>Группа:</b> {group}")
                if data['room']:
                    result_lines.append(f"🏫 <b>Аудитория:</b> {data['room']}")
                if data['type']:
                    result_lines.append(f"📌 <b>Тип:</b> {data['type']}")
                result_lines.append("─" * 30)
        return "\n".join(result)

# --- КЛАВИАТУРЫ ---
def get_user_type_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👨‍🎓 Студент"), KeyboardButton(text="👨‍🏫 Преподаватель")]], resize_keyboard=True)

def get_main_keyboard_student():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📅 Текущая неделя"), KeyboardButton(text="📅 Следующая неделя")],
        [KeyboardButton(text="🔍 Поиск преподавателя"), KeyboardButton(text="📋 Список групп")],
        [KeyboardButton(text="🔄 Обновить"), KeyboardButton(text="🔄 Сменить пользователя")]
    ], resize_keyboard=True)

def get_main_keyboard_correspondence():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Консультации сегодня"), KeyboardButton(text="📋 Все консультации")],
        [KeyboardButton(text="🔄 Обновить"), KeyboardButton(text="🔄 Сменить пользователя")]
    ], resize_keyboard=True)

def get_main_keyboard_teacher():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📅 Текущая неделя"), KeyboardButton(text="📅 Следующая неделя")],
        [KeyboardButton(text="🔄 Обновить"), KeyboardButton(text="🔄 Сменить пользователя")]
    ], resize_keyboard=True)

def get_week_selection_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📅 Текущая неделя"), KeyboardButton(text="📅 Следующая неделя")],
        [KeyboardButton(text="◀️ Назад")]
    ], resize_keyboard=True)

def get_days_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Понедельник"), KeyboardButton(text="Вторник")],
        [KeyboardButton(text="Среда"), KeyboardButton(text="Четверг")],
        [KeyboardButton(text="Пятница"), KeyboardButton(text="Суббота")],
        [KeyboardButton(text="◀️ Назад")]
    ], resize_keyboard=True)

def get_day_selection_keyboard(user_type, week_type):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Понедельник"), KeyboardButton(text="Вторник")],
        [KeyboardButton(text="Среда"), KeyboardButton(text="Четверг")],
        [KeyboardButton(text="Пятница"), KeyboardButton(text="Суббота")],
        [KeyboardButton(text="◀️ Назад")]
    ], resize_keyboard=True)

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
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="◀️ Назад")]], resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---
dp = Dispatcher()
schedule = ScheduleMaster()
bot = None

@dp.message(Command('start'))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await schedule.ensure_data_loaded()
    welcome_text = (
        "🎓 <b>РГАТУ Расписание</b>\n\n"
        "📚 <b>Что умеет этот бот?</b>\n"
        "⭐ Вся информация о расписании получена с официального сайта РГАТУ имени П.А. Соловьева.\n\n"
        "🔹 Просмотр расписания по группам (с учётом подгрупп)\n"
        "🔹 Просмотр расписания по преподавателям\n"
        "🔹 Просмотр расписания для заочников (консультации)\n"
        "🔹 Автоматическое определение чётной/нечётной недели\n"
        "🔹 Автоматическое обновление данных с сайта\n\n"
        "👇 <b>Вы студент или преподаватель?</b>"
    )
    if user_id in user_data:
        user_type = user_data[user_id].get('type')
        group_type = user_data[user_id].get('group_type', '')
        if user_type == 'student':
            if group_type == 'correspondence':
                week_display, _, _ = get_week_type()
                await message.answer(f"👋 С возвращением! Ваша группа: {user_data[user_id].get('display_name', '')}\n{week_display}", reply_markup=get_main_keyboard_correspondence())
            else:
                display = user_data[user_id].get('display_name', user_data[user_id].get('group', ''))
                week_display, _, _ = get_week_type()
                await message.answer(f"👋 С возвращением! Ваша группа: {display}\n{week_display}", reply_markup=get_main_keyboard_student())
        else:
            teacher_name = user_data[user_id].get('teacher_name', '')
            week_display, _, _ = get_week_type()
            await message.answer(f"👋 С возвращением! Вы зарегистрированы как преподаватель: {teacher_name}\n{week_display}", reply_markup=get_main_keyboard_teacher())
        return
    await message.answer(welcome_text, parse_mode='HTML', reply_markup=get_user_type_keyboard())
    await state.set_state(States.waiting_for_user_type)

@dp.message(States.waiting_for_user_type)
async def process_user_type(message: Message, state: FSMContext):
    if message.text == "👨‍🎓 Студент":
        await state.update_data(user_type='student')
        await message.answer("Введите вашу группу:\n\nДля очников: ИВБ-24, ИВБ-25, ВРБ-21\nДля заочников: ЗВС-22, ЗВС-24 (начинается с буквы З)", reply_markup=get_back_keyboard())
        await state.set_state(States.waiting_for_group)
    elif message.text == "👨‍🏫 Преподаватель":
        await state.update_data(user_type='teacher')
        await message.answer("Введите вашу фамилию (например: Иванов, Петрова):", reply_markup=get_back_keyboard())
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
    user_data[user_id] = {'type': 'teacher', 'teacher_name': teacher_name, 'display_name': teacher_name}
    week_display, _, _ = get_week_type()
    await message.answer(f"✅ Вы зарегистрированы как преподаватель: {teacher_name}\n{week_display}\n\nТеперь вы можете смотреть своё расписание.", reply_markup=get_main_keyboard_teacher())
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
    await schedule.ensure_data_loaded()
    exact_group, group_type = schedule.find_group(group_name)
    if not exact_group:
        await msg.delete()
        await message.answer(f"❌ Группа '{group_name}' не найдена.\n\nНажмите '📋 Список групп' чтобы увидеть все группы.", reply_markup=get_main_keyboard_student())
        await state.clear()
        return
    user_data[user_id] = {'type': 'student', 'group': exact_group, 'group_type': group_type, 'display_name': exact_group}
    week_display, _, _ = get_week_type()
    await msg.delete()
    if group_type == "fulltime":
        subgroups = schedule.get_subgroups(exact_group)
        if subgroups:
            await state.update_data(base_group=exact_group, group_type=group_type)
            await message.answer(f"✅ Найдена группа {exact_group}\n\nУ этой группы есть подгруппы. Выберите свою:", reply_markup=get_subgroups_keyboard(subgroups))
            await state.set_state(States.waiting_for_subgroup)
            return
        today = get_today_name()
        result = schedule.get_day_schedule_fulltime(exact_group, None, today)
        if result:
            await message.answer(result, parse_mode='HTML')
        else:
            await message.answer(f"📭 На сегодня ({today}) занятий нет")
        await message.answer(f"✅ Группа {exact_group} сохранена!\n{week_display}", reply_markup=get_main_keyboard_student())
    else:
        result = schedule.get_consultations_for_group(exact_group)
        if result:
            await message.answer(result, parse_mode='HTML')
        else:
            await message.answer(f"📭 Для группы {exact_group} консультации не найдены")
        await message.answer(f"✅ Группа {exact_group} сохранена!\n{week_display}", reply_markup=get_main_keyboard_correspondence())
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
    if not message.text.startswith("Подгруппа "):
        await message.answer("Пожалуйста, выберите подгруппу из меню")
        return
    try:
        subgroup = int(message.text.replace("Подгруппа ", ""))
    except:
        await message.answer("Пожалуйста, выберите подгруппу из меню")
        return
    display_name = f"{base_group}-{subgroup}"
    user_data[user_id] = {'type': 'student', 'group': base_group, 'subgroup': subgroup, 'group_type': group_type, 'display_name': display_name}
    week_display, _, _ = get_week_type()
    today = get_today_name()
    await schedule.ensure_data_loaded()
    result = schedule.get_day_schedule_fulltime(base_group, subgroup, today)
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 На сегодня ({today}) занятий нет")
    await message.answer(f"✅ Группа {display_name} сохранена!\n{week_display}", reply_markup=get_main_keyboard_student())
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
        await message.answer("❌ Для заочников расписание по дням недели не предусмотрено.\nИспользуйте кнопку '📋 Консультации'")
        return
    await state.update_data(week_offset=0)
    if user_type == 'student':
        await message.answer("Выберите день (текущая неделя):", reply_markup=get_day_selection_keyboard('student', 'current'))
        await state.set_state(States.waiting_for_day)
    else:
        await message.answer("Выберите день (текущая неделя):", reply_markup=get_day_selection_keyboard('teacher', 'current'))
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
        await message.answer("❌ Для заочников расписание по дням недели не предусмотрено.\nИспользуйте кнопку '📋 Консультации'")
        return
    await state.update_data(week_offset=1)
    if user_type == 'student':
        await message.answer("Выберите день (следующая неделя):", reply_markup=get_day_selection_keyboard('student', 'next'))
        await state.set_state(States.waiting_for_day)
    else:
        await message.answer("Выберите день (следующая неделя):", reply_markup=get_day_selection_keyboard('teacher', 'next'))
        await state.set_state(States.waiting_for_teacher_day)

@dp.message(States.waiting_for_day)
async def show_group_schedule(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
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
    await schedule.ensure_data_loaded()
    subgroup = group_info.get('subgroup')
    result = schedule.get_day_schedule_fulltime(group_info['group'], subgroup, day_name, week_offset)
    await msg.delete()
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        week_text = "следующей" if week_offset == 1 else "текущей"
        await message.answer(f"📭 На {day_name} {week_text} недели занятий нет")
    await message.answer("Выберите другой день:", reply_markup=get_day_selection_keyboard('student', 'current' if week_offset == 0 else 'next'))

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
    await schedule.ensure_data_loaded()
    results = schedule.search_teacher(teacher_name, day_name, week_offset)
    await msg.delete()
    if results:
        formatted = schedule.format_teacher_schedule(results, teacher_name, day_name, week_offset)
        await message.answer(formatted, parse_mode='HTML')
    else:
        week_text = "следующей" if week_offset == 1 else "текущей"
        await message.answer(f"📭 Для преподавателя {teacher_name} на {day_name} {week_text} недели занятий нет")
    await message.answer("Выберите другой день:", reply_markup=get_day_selection_keyboard('teacher', 'current' if week_offset == 0 else 'next'))

@dp.message(F.text == "🔍 Поиск преподавателя")
async def student_search_teacher(message: Message, state: FSMContext):
    if message.from_user.id not in user_data:
        await message.answer("❌ Сначала зарегистрируйтесь через /start")
        return
    group_type = user_data[message.from_user.id].get('group_type', '')
    if group_type == 'correspondence':
        await message.answer("❌ Для заочников поиск преподавателя не поддерживается.")
        return
    await message.answer("👨‍🏫 Введите фамилию преподавателя (например: Иванов, Петрова, Комаров):", reply_markup=get_back_keyboard())
    await state.set_state(States.waiting_for_student_teacher_search)

@dp.message(States.waiting_for_student_teacher_search)
async def process_student_teacher_name(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())
        return
    teacher_name = message.text.strip()
    await state.update_data(search_teacher_name=teacher_name)
    await message.answer("📅 Выберите неделю для поиска:", reply_markup=get_week_selection_keyboard())
    await state.set_state(States.waiting_for_student_teacher_week)

@dp.message(States.waiting_for_student_teacher_week)
async def process_student_teacher_week(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.update_data(search_teacher_name=None)
        await message.answer("👨‍🏫 Введите фамилию преподавателя:", reply_markup=get_back_keyboard())
        await state.set_state(States.waiting_for_student_teacher_search)
        return
    if message.text == "📅 Текущая неделя":
        week_offset = 0
    elif message.text == "📅 Следующая неделя":
        week_offset = 1
    else:
        await message.answer("Пожалуйста, выберите неделю из меню")
        return
    await state.update_data(search_week_offset=week_offset)
    await message.answer("📅 Выберите день для поиска:", reply_markup=get_days_keyboard())
    await state.set_state(States.waiting_for_student_teacher_day)

@dp.message(States.waiting_for_student_teacher_day)
async def process_student_teacher_day(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await message.answer("📅 Выберите неделю для поиска:", reply_markup=get_week_selection_keyboard())
        await state.set_state(States.waiting_for_student_teacher_week)
        return
    day_name = message.text
    if day_name not in DAYS:
        await message.answer("Пожалуйста, выберите день из меню")
        return
    data = await state.get_data()
    teacher_name = data.get('search_teacher_name')
    week_offset = data.get('search_week_offset', 0)
    msg = await message.answer(f"🔍 Ищу преподавателя {teacher_name} на {day_name}...")
    await schedule.ensure_data_loaded()
    results = schedule.search_teacher(teacher_name, day_name, week_offset)
    await msg.delete()
    if results:
        formatted = schedule.format_student_teacher_search(results, teacher_name, day_name, week_offset)
        await message.answer(formatted, parse_mode='HTML')
    else:
        week_text = "следующей" if week_offset == 1 else "текущей"
        await message.answer(f"❌ Преподаватель {teacher_name} не найден на {day_name} {week_text} неделе")
    await message.answer("Главное меню:", reply_markup=get_main_keyboard_student())
    await state.clear()

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
    await schedule.ensure_data_loaded()
    result = schedule.get_consultations_for_group(group_name)
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 Для группы {group_name} консультации не найдены")

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
    await schedule.ensure_data_loaded()
    result = schedule.get_consultations_for_group(group_name)
    if result:
        await message.answer(result, parse_mode='HTML')
    else:
        await message.answer(f"📭 Для группы {group_name} консультации не найдены")

@dp.message(F.text == "📋 Список групп")
async def show_groups(message: Message):
    await schedule.ensure_data_loaded()
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
    await schedule.ensure_data_loaded()
    await msg.delete()
    text = f"📊 <b>РЕЗУЛЬТАТ</b>\n\n✅ Данные обновлены: {schedule.last_update.strftime('%d.%m.%Y %H:%M:%S') if schedule.last_update else 'только что'}"
    user_type = user_data.get(message.from_user.id, {}).get('type', 'student')
    group_type = user_data.get(message.from_user.id, {}).get('group_type', '')
    if user_type == 'teacher' or group_type == 'correspondence':
        await message.answer(text, parse_mode='HTML', reply_markup=get_main_keyboard_teacher())
    else:
        await message.answer(text, parse_mode='HTML', reply_markup=get_main_keyboard_student())

@dp.message(F.text == "🔄 Сменить пользователя")
async def change_user(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in user_data:
        del user_data[user_id]
    await state.clear()
    await message.answer("👋 Вы сменили пользователя.\n\nВы студент или преподаватель?", reply_markup=get_user_type_keyboard())
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
    global bot
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    await schedule.start()
    await schedule.ensure_data_loaded()
    me = await bot.get_me()
    print(f"✅ Бот @{me.username} успешно подключён к Telegram API")
    today = get_today_name()
    week_display, _, _ = get_week_type()
    print(f"📅 Сегодня: {today}")
    print(f"📅 Текущая неделя: {week_display}")
    print(f"✅ Бот готов!")
    await dp.start_polling(bot)
from aiohttp import web

PORT = int(os.environ.get('PORT', 10000))

async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"🌐 Веб-сервер запущен на порту {PORT}")

async def main():
    await start_web()  # Добавить эту строку
    # ... остальной код
if __name__ == '__main__':
    asyncio.run(main())