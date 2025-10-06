import re
from html import escape

def markdown_to_html(text: str) -> str:
    """
    Конвертируем Markdown и Gemini-ответ в безопасный HTML для Telegram
    """
   
    # 1. Удаляем C-стиль комментарии /* ... */
    text = re.sub(r'/\*[\s\S]*?\*/', '', text, flags=re.DOTALL)
    
    # 2. Многострочные код-блоки (обрабатываем ПЕРВЫМИ, чтобы защитить содержимое)
    code_blocks = []
    def code_block_replacer(match):
        content = escape(match.group(1))
        placeholder = f"___CODE_BLOCK_{len(code_blocks)}___"
        code_blocks.append(f"<pre>{content}</pre>")
        return placeholder
   
    text = re.sub(r'```(?:\w+)?\n([\s\S]*?)```', code_block_replacer, text, flags=re.DOTALL)
    
    # 3. Таблицы - конвертируем в моноширинный текст для Telegram
    table_blocks = []
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
                    # Escape содержимого ячеек
                    line_parts.append(escape(cell).ljust(col_widths[i]))
            result += ' | '.join(line_parts) + '\n'
            
            # После заголовка добавляем разделитель
            if idx == 0:
                separator_parts = ['-' * width for width in col_widths]
                result += '-+-'.join(separator_parts) + '\n'
        
        result += '</pre>'
        
        placeholder = f"___TABLE_BLOCK_{len(table_blocks)}___"
        table_blocks.append(result)
        return placeholder
    
    # Ищем таблицы (заголовок | разделитель | строки)
    text = re.sub(
        r'(?:^|\n)(\|.+\|\n\|[\s:|\-]+\|\n(?:\|.+\|\n?)*)',
        table_replacer,
        text,
        flags=re.MULTILINE
    )
    
    # 4. Инлайн код (защищаем от дальнейшей обработки)
    inline_codes = []
    def inline_code_replacer(match):
        placeholder = f"___INLINE_CODE_{len(inline_codes)}___"
        inline_codes.append(f'<code>{escape(match.group(1))}</code>')
        return placeholder
       
    text = re.sub(r'`([^`]+)`', inline_code_replacer, text)
    
    # 5. Жирный
    text = re.sub(r'\*\*([^*]+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+?)__', r'<b>\1</b>', text)
    
    # 6. Курсив
    text = re.sub(r'\*([^*]+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+?)_', r'<i>\1</i>', text)
    
    # 7. Зачёркнутый
    text = re.sub(r'~~([^~]+?)~~', r'<s>\1</s>', text)
    
    # 8. Ссылки
    def link_replacer(match):
        text_content = match.group(1)
        url = escape(match.group(2))
        return f'<a href="{url}">{text_content}</a>'
       
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_replacer, text)
    
    # 9. Спойлеры
    text = re.sub(r'\|\|(.+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text)
   
    # Восстанавливаем защищенные блоки (в обратном порядке)
    for i, code in enumerate(inline_codes):
        text = text.replace(f"___INLINE_CODE_{i}___", code)
    
    for i, table in enumerate(table_blocks):
        text = text.replace(f"___TABLE_BLOCK_{i}___", table)
    
    for i, code in enumerate(code_blocks):
        text = text.replace(f"___CODE_BLOCK_{i}___", code)
    
    return text


def smart_split_message(text: str, max_length: int = 4096) -> list[str]:
    """
    Умное разделение сообщения с учетом HTML-тегов, таблиц и целостности текста
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    
    # Находим все таблицы и код-блоки
    table_pattern = r'<pre>[\s\S]*?</pre>'
    
    # Сохраняем позиции специальных блоков
    special_blocks = []
    
    for match in re.finditer(table_pattern, text):
        special_blocks.append((match.start(), match.end(), match.group(0)))
    
    # Сортируем по позиции
    special_blocks.sort(key=lambda x: x[0])
    
    # Разбиваем текст на блоки
    blocks = []
    last_pos = 0
    
    for start, end, block_content in special_blocks:
        # Добавляем текст до специального блока
        if start > last_pos:
            pre_text = text[last_pos:start].strip()
            if pre_text:
                blocks.append(('text', pre_text))
        
        # Добавляем специальный блок целиком
        blocks.append(('special', block_content))
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
    open_tags = []
    
    def get_open_tags_string():
        """Получить строку открывающих тегов"""
        return ''.join(open_tags)
    
    def get_close_tags_string():
        """Получить строку закрывающих тегов в обратном порядке"""
        close_tags = []
        for tag in reversed(open_tags):
            tag_name = tag[1:].split()[0].rstrip('>')
            close_tags.append(f'</{tag_name}>')
        return ''.join(close_tags)
    
    def track_tags(text_chunk):
        """Отслеживать открытые теги в тексте"""
        nonlocal open_tags
        
        # Находим все теги
        tag_pattern = r'<(/?)(\w+)(?:\s[^>]*)?>|<(tg-spoiler)>|</(tg-spoiler)>'
        
        for match in re.finditer(tag_pattern, text_chunk):
            if match.group(3):  # <tg-spoiler>
                open_tags.append('<tg-spoiler>')
            elif match.group(4):  # </tg-spoiler>
                if open_tags and open_tags[-1] == '<tg-spoiler>':
                    open_tags.pop()
            elif match.group(1):  # Закрывающий тег
                tag_name = match.group(2)
                # Ищем соответствующий открывающий тег
                for i in range(len(open_tags) - 1, -1, -1):
                    if open_tags[i].startswith(f'<{tag_name}'):
                        open_tags.pop(i)
                        break
            else:  # Открывающий тег
                open_tags.append(match.group(0))
    
    for block_type, block_content in blocks:
        block_len = len(block_content)
        
        # Если блок слишком большой - пытаемся разбить
        if block_type == 'special' and block_len > max_length:
            # Сохраняем текущую часть
            if current_part.strip():
                parts.append(current_part.strip())
                current_part = ""
                open_tags = []
            
            # Для <pre> блоков пытаемся разбить по строкам
            if block_content.startswith('<pre>'):
                inner = block_content[5:-6]  # Убираем <pre> и </pre>
                lines = inner.split('\n')
                temp_block = ""
                
                for line in lines:
                    if len(temp_block) + len(line) + 12 <= max_length:  # +12 для <pre></pre>
                        temp_block += line + '\n'
                    else:
                        if temp_block:
                            parts.append(f'<pre>{temp_block}</pre>')
                        temp_block = line + '\n'
                
                if temp_block:
                    parts.append(f'<pre>{temp_block}</pre>')
            else:
                # Крайний случай - режем по символам
                for i in range(0, block_len, max_length - 100):
                    parts.append(block_content[i:i + max_length - 100])
            continue
        
        # Если добавление блока превысит лимит
        if len(current_part) + block_len + 2 > max_length:
            # Закрываем открытые теги и сохраняем часть
            if current_part.strip():
                parts.append(current_part.strip() + get_close_tags_string())
                current_part = get_open_tags_string()
                
            # Если блок все еще не помещается - начинаем с чистого листа
            if len(current_part) + block_len > max_length:
                if current_part != get_open_tags_string():
                    parts.append(current_part.strip() + get_close_tags_string())
                current_part = ""
                open_tags = []
                
                # Разбиваем текстовый блок
                if block_type == 'text':
                    sentences = re.split(r'([.!?]+\s+|\n\n+)', block_content)
                    
                    for sentence in sentences:
                        if not sentence.strip():
                            continue
                        
                        if len(current_part) + len(sentence) <= max_length:
                            current_part += sentence
                            track_tags(sentence)
                        else:
                            if current_part.strip():
                                parts.append(current_part.strip() + get_close_tags_string())
                            
                            current_part = get_open_tags_string() + sentence
                            open_tags_backup = open_tags.copy()
                            open_tags = []
                            track_tags(current_part)
                else:
                    current_part = block_content
                    track_tags(block_content)
            else:
                if current_part and not current_part.endswith('\n\n'):
                    current_part += "\n\n"
                current_part += block_content
                track_tags(block_content)
        else:
            # Добавляем блок к текущей части
            if current_part and not current_part.endswith('\n\n') and block_type != 'special':
                current_part += "\n\n"
            current_part += block_content
            track_tags(block_content)
    
    # Добавляем последнюю часть с закрытием тегов
    if current_part.strip():
        parts.append(current_part.strip() + get_close_tags_string())
    
    return parts