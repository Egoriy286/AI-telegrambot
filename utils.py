import re
from html import escape

def markdown_to_html(text: str) -> str:
    """
    Конвертируем Markdown и Gemini-ответ в безопасный HTML для Telegram
    """
   
    # 1. Удаляем C-стиль комментарии /* ... */
    text = re.sub(r'/\*[\s\S]*?\*/', '', text, flags=re.DOTALL)
    
    # 2. Многострочные код-блоки
    def code_block_replacer(match):
        content = escape(match.group(1))
        return f"<pre>{content}</pre>"
   
    text = re.sub(r'```(?:\w+)?\n([\s\S]*?)```', code_block_replacer, text, flags=re.DOTALL)
    
    # 3. Таблицы - конвертируем в моноширинный текст для Telegram
    def table_replacer(match):
        table_text = match.group(0)
        lines = [line.strip() for line in table_text.strip().split('\n') if line.strip()]
        
        if len(lines) < 2:
            return table_text
        
        # Парсим все строки
        rows = []
        for i, line in enumerate(lines):
            if i == 1:  # Пропускаем разделительную строку (---|---|---)
                continue
            cells = [cell.strip() for cell in line.split('|')]
            # Убираем пустые ячейки по краям (из-за | в начале/конце)
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
        
        if not rows:
            return table_text
        
        # Вычисляем максимальную ширину для каждой колонки
        num_cols = len(rows[0])
        col_widths = [0] * num_cols
        
        for row in rows:
            for i, cell in enumerate(row):
                if i < num_cols:
                    col_widths[i] = max(col_widths[i], len(cell))
        
        # Формируем выровненную таблицу
        result = '<pre>\n'
        
        for idx, row in enumerate(rows):
            line_parts = []
            for i, cell in enumerate(row):
                if i < num_cols:
                    # Выравниваем по левому краю с отступом
                    line_parts.append(cell.ljust(col_widths[i]))
            result += ' | '.join(line_parts) + '\n'
            
            # После заголовка добавляем разделитель
            if idx == 0:
                separator_parts = ['-' * width for width in col_widths]
                result += '-+-'.join(separator_parts) + '\n'
        
        result += '</pre>'
        return result
    
    # Ищем таблицы (заголовок | разделитель | строки)
    text = re.sub(
        r'(?:^|\n)(\|.+\|\n\|[\s:|\-]+\|\n(?:\|.+\|\n?)*)',
        table_replacer,
        text,
        flags=re.MULTILINE
    )
    
    # 4. Инлайн код
    def inline_code_replacer(match):
        return f'<code>{escape(match.group(1))}</code>'
       
    text = re.sub(r'`([^`]+)`', inline_code_replacer, text)
    
    # 5. Жирный
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    
    # 6. Курсив
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    
    # 7. Зачёркнутый
    text = re.sub(r'~~([^~]+)~~', r'<s>\1</s>', text)
    
    # 8. Ссылки
    def link_replacer(match):
        text_content = escape(match.group(1))
        url = escape(match.group(2))
        return f'<a href="{url}">{text_content}</a>'
       
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replacer, text)
    
    # 9. Спойлеры
    text = re.sub(r'\|\|(.+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text)
   
    return text


def smart_split_message(text: str, max_length: int = 4096) -> list[str]:
    """
    Умное разделение сообщения с учетом HTML-тегов, таблиц и целостности текста
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_part = ""
    
    # Разбиваем текст на блоки (параграфы, таблицы, код-блоки)
    blocks = []
    
    # Находим все таблицы и код-блоки
    table_pattern = r'<pre>\n(?:.*?\|.*?\n)+</pre>'
    code_pattern = r'<pre>[\s\S]*?</pre>'
    
    # Сохраняем позиции специальных блоков
    special_blocks = []
    
    for match in re.finditer(table_pattern, text, re.MULTILINE):
        special_blocks.append((match.start(), match.end(), 'table'))
    
    for match in re.finditer(code_pattern, text, re.MULTILINE):
        # Проверяем, не является ли этот блок таблицей
        is_table = any(
            match.start() >= start and match.end() <= end 
            for start, end, btype in special_blocks if btype == 'table'
        )
        if not is_table:
            special_blocks.append((match.start(), match.end(), 'code'))
    
    # Сортируем по позиции
    special_blocks.sort(key=lambda x: x[0])
    
    # Разбиваем текст на блоки
    last_pos = 0
    for start, end, block_type in special_blocks:
        # Добавляем текст до специального блока
        if start > last_pos:
            pre_text = text[last_pos:start].strip()
            if pre_text:
                blocks.append(('text', pre_text))
        
        # Добавляем специальный блок целиком
        blocks.append((block_type, text[start:end]))
        last_pos = end
    
    # Добавляем оставшийся текст
    if last_pos < len(text):
        remaining = text[last_pos:].strip()
        if remaining:
            blocks.append(('text', remaining))
    
    # Если нет специальных блоков, разбиваем весь текст как обычный
    if not blocks:
        blocks = [('text', text)]
    
    # Собираем части сообщения
    current_part = ""
    
    for block_type, block_content in blocks:
        block_len = len(block_content)
        
        # Если блок (таблица/код) слишком большой - выделяем его отдельно
        if block_type in ['table', 'code'] and block_len > max_length:
            # Сохраняем текущую часть
            if current_part.strip():
                parts.append(current_part.strip())
                current_part = ""
            
            # Разбиваем большой блок
            for i in range(0, block_len, max_length - 100):
                chunk = block_content[i:i + max_length - 100]
                parts.append(chunk)
            continue
        
        # Если добавление блока превысит лимит
        if len(current_part) + block_len + 2 > max_length:
            # Если это таблица или код - сохраняем текущую часть и начинаем новую
            if block_type in ['table', 'code']:
                if current_part.strip():
                    parts.append(current_part.strip())
                current_part = block_content
            else:
                # Для обычного текста - разбиваем по предложениям/параграфам
                if current_part.strip():
                    parts.append(current_part.strip())
                    current_part = ""
                
                # Разбиваем текстовый блок на предложения
                sentences = re.split(r'([.!?]+\s+|\n\n+)', block_content)
                
                for sentence in sentences:
                    if not sentence.strip():
                        continue
                    
                    if len(current_part) + len(sentence) + 2 <= max_length:
                        current_part += sentence
                    else:
                        if current_part.strip():
                            parts.append(current_part.strip())
                        
                        # Если одно предложение слишком длинное
                        if len(sentence) > max_length:
                            # Разбиваем по словам
                            words = sentence.split()
                            current_part = ""
                            
                            for word in words:
                                if len(current_part) + len(word) + 1 <= max_length:
                                    current_part += word + " "
                                else:
                                    if current_part.strip():
                                        parts.append(current_part.strip())
                                    current_part = word + " "
                        else:
                            current_part = sentence
        else:
            # Добавляем блок к текущей части
            if current_part and not current_part.endswith('\n\n'):
                current_part += "\n\n"
            current_part += block_content
    
    # Добавляем последнюю часть
    if current_part.strip():
        parts.append(current_part.strip())
    
    return parts