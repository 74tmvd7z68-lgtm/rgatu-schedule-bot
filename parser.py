import aiohttp
from bs4 import BeautifulSoup
import asyncio

class ScheduleParser:
    """Класс для сбора расписания с сайта"""
    
    def __init__(self, base_url):
        self.base_url = base_url
        self.session = None
    
    async def start(self):
        """Запуск соединения с сайтом"""
        self.session = aiohttp.ClientSession()
    
    async def stop(self):
        """Закрытие соединения"""
        if self.session:
            await self.session.close()
    
    async def get_page(self, url):
        """Загрузка страницы с сайта"""
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    print(f"Ошибка загрузки: {response.status}")
                    return None
        except Exception as e:
            print(f"Ошибка: {e}")
            return None
    
    async def parse_group_schedule(self, group_name):
        """
        Парсинг расписания для конкретной группы
        Например: ВС-6, ИС-7 и т.д.
        """
        # Формируем адрес страницы с расписанием группы
        # ВАЖНО: этот адрес нужно уточнить для вашего сайта!
        url = f"{self.base_url}/schedule/{group_name}.html"
        
        # Загружаем страницу
        html = await self.get_page(url)
        if not html:
            return None
        
        # Разбираем HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # Ищем таблицу с расписанием
        table = soup.find('table')
        if not table:
            return None
        
        # Собираем расписание
        schedule = {
            'group': group_name,
            'days': []
        }
        
        # Проходим по всем строкам таблицы
        rows = table.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 4:  # Если есть все колонки
                lesson = {
                    'day': cols[0].text.strip(),
                    'number': cols[1].text.strip(),
                    'subject': cols[2].text.strip(),
                    'teacher': cols[3].text.strip(),
                    'room': cols[4].text.strip() if len(cols) > 4 else ''
                }
                schedule['days'].append(lesson)
        
        return schedule
    
    async def format_schedule_text(self, group_name, day=None):
        """
        Форматирует расписание в читаемый текст
        """
        schedule = await self.parse_group_schedule(group_name)
        
        if not schedule:
            return f"Не удалось найти расписание для группы {group_name}"
        
        # Словарь для перевода дней
        days_ru = {
            'ПН': 'ПОНЕДЕЛЬНИК',
            'ВТ': 'ВТОРНИК',
            'СР': 'СРЕДА',
            'ЧТ': 'ЧЕТВЕРГ',
            'ПТ': 'ПЯТНИЦА',
            'СБ': 'СУББОТА'
        }
        
        text = f"📚 РАСПИСАНИЕ ГРУППЫ {group_name}\n"
        text += "─" * 30 + "\n\n"
        
        current_day = None
        
        for lesson in schedule['days']:
            # Если указан конкретный день, показываем только его
            if day and lesson['day'] != day:
                continue
            
            # Если новый день, пишем его название
            if lesson['day'] != current_day:
                current_day = lesson['day']
                day_name = days_ru.get(current_day, current_day)
                text += f"\n🔹 {day_name}\n"
            
            # Добавляем занятие
            text += f"{lesson['number']} пара: {lesson['subject']}\n"
            if lesson['teacher']:
                text += f"   👤 {lesson['teacher']}\n"
            if lesson['room']:
                text += f"   🏫 {lesson['room']}\n"
            text += "\n"
        
        return text