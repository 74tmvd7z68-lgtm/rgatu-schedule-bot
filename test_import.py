from parser import ScheduleParser

print("Пробуем импортировать...")
parser = ScheduleParser("test")
print("Класс найден!")
print(parser.format_schedule_text("Тест"))
input("Нажми Enter для выхода...")