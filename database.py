# database.py - Работа с базой данных
import sqlite3
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
import os

from dotenv import load_dotenv

load_dotenv()


FREE_DAILY_LIMIT = int(os.getenv("FREE_DAILY_LIMIT"))
PREMIUM_DAILY_LIMIT = int(os.getenv("PREMIUM_DAILY_LIMIT"))
class Database:
    def __init__(self, db_name: str = "bot_database.db"):
        self.db_name = db_name
        self.init_db()
    
    def get_connection(self):
        """Создает подключение к БД"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                today_requests INTEGER DEFAULT 0,
                last_request_date DATE,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0.0
            )
        """)
        
        # Таблица истории сообщений
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Таблица подписок
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_date TIMESTAMP,
                period TEXT,
                price INTEGER,
                is_active BOOLEAN DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Индексы
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_messages ON message_history(user_id, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_last_activity ON users(last_activity)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions ON subscriptions(user_id, is_active)")
        
        conn.commit()
        conn.close()
    
    def add_user(self, user_id: int, username: str, full_name: str):
        """Добавление нового пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO users (user_id, username, full_name, last_request_date) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_activity = CURRENT_TIMESTAMP
        """, (user_id, username, full_name, date.today()))
        
        conn.commit()
        conn.close()

    def check_user(self, user_id: int) -> bool:
        """Проверка, существует ли пользователь в базе по user_id"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 1
            FROM users
            WHERE user_id = ?
            LIMIT 1
        """, (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        return bool(result)

    
    def add_message(self, user_id: int, role: str, content: str):
        """Добавление сообщения в историю"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO message_history (user_id, role, content)
            VALUES (?, ?, ?)
        """, (user_id, role, content))
        
        conn.commit()
        conn.close()
    
    def get_history(self, user_id: int, limit: int = 20) -> List[Dict]:
        """Получение истории сообщений"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT role, content, timestamp
            FROM message_history
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, limit))
        
        messages = cursor.fetchall()
        conn.close()
        
        return [
            {"role": msg['role'], "content": msg['content'], "timestamp": msg['timestamp']}
            for msg in reversed(messages)
        ]
    
    def clear_history(self, user_id: int):
        """Очистка истории пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_history WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    def get_remaining_requests(self, user_id: int) -> int:
        """Получение оставшихся запросов на сегодня"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT today_requests, last_request_date
            FROM users
            WHERE user_id = ?
        """, (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return FREE_DAILY_LIMIT
        
        has_active_subscription = self.get_subscription_info(user_id)['is_active']
        
        today = date.today()
        last_date = datetime.strptime(result['last_request_date'], '%Y-%m-%d').date() if result['last_request_date'] else None
        daily_limit = PREMIUM_DAILY_LIMIT if has_active_subscription else FREE_DAILY_LIMIT
        if last_date != today:
            return daily_limit
        
        return max(0, daily_limit - result['today_requests'])
    
    def update_stats(self, user_id: int, input_tokens: int, output_tokens: int, cost: float):
        """Обновление статистики пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        today = date.today()
        
        # Проверяем, нужно ли сбросить счетчик
        cursor.execute("SELECT last_request_date FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result:
            last_date = datetime.strptime(result['last_request_date'], '%Y-%m-%d').date() if result['last_request_date'] else None
            if last_date != today:
                cursor.execute("""
                    UPDATE users
                    SET today_requests = 1,
                        last_request_date = ?
                    WHERE user_id = ?
                """, (today, user_id))
            else:
                cursor.execute("""
                    UPDATE users
                    SET today_requests = today_requests + 1
                    WHERE user_id = ?
                """, (user_id,))
        
        cursor.execute("""
            UPDATE users
            SET message_count = message_count + 1,
                total_input_tokens = total_input_tokens + ?,
                total_output_tokens = total_output_tokens + ?,
                total_cost = total_cost + ?,
                last_activity = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (input_tokens, output_tokens, cost, user_id))
        
        conn.commit()
        conn.close()
    
    def get_subscription_info(self, user_id: int) -> Dict:
        """Получение информации о подписке"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT end_date, is_active
            FROM subscriptions
            WHERE user_id = ? AND is_active = 1
            ORDER BY end_date DESC
            LIMIT 1
        """, (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            end_date = datetime.strptime(result['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            if end_date > datetime.now():
                return {
                    'is_active': True,
                    'expires_at': end_date.strftime('%d.%m.%Y')
                }
            else:
                # Деактивируем истекшую подписку
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE subscriptions 
                    SET is_active = 0 
                    WHERE user_id = ? AND end_date < ?
                """, (user_id, datetime.now()))
                conn.commit()
                conn.close()
        
        return {'is_active': False, 'expires_at': None}
    
    def add_subscription(self, user_id: int, days: int, price: int):
        """Добавление подписки"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Определяем период
        period = ""
        if days == 7:
            period = "week"
        elif days == 30:
            period = "month"
        elif days == 60:
            period = "2 month"
        
        start_date = datetime.now()
        end_date = start_date + timedelta(days=days)
        
        cursor.execute("""
            INSERT INTO subscriptions (user_id, start_date, end_date, period, price)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, start_date, end_date, period, price))
        
        conn.commit()
        conn.close()
    
    def get_user_stats(self, user_id: int) -> Optional[Dict]:
        """Получение подробной статистики пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                u.username,
                u.full_name,
                u.registration_date,
                u.last_activity,
                u.message_count,
                u.today_requests,
                u.total_input_tokens,
                u.total_output_tokens,
                u.total_cost
            FROM users u
            WHERE u.user_id = ?
        """, (user_id,))
        
        result = cursor.fetchone()
        conn.close()

        if result:
            reg_date = datetime.strptime(result['registration_date'], '%Y-%m-%d %H:%M:%S')
            last_act = datetime.strptime(result['last_activity'], '%Y-%m-%d %H:%M:%S')
            
            return {
                "username": result['username'],
                "full_name": result['full_name'],
                "registration_date": reg_date.strftime('%d.%m.%Y'),
                "last_activity": last_act.strftime('%d.%m.%Y %H:%M:%S'),
                "total_messages": result['message_count'],
                "today_requests": result['today_requests'],
                "total_input_tokens": result['total_input_tokens'],
                "total_output_tokens": result['total_output_tokens'],
                "total_cost": result['total_cost']
            }
        return None
    
    def get_user_last_act(self, user_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                u.last_activity,
                CURRENT_TIMESTAMP AS current_time
            FROM users u
            WHERE u.user_id = ?
        """, (user_id,))
        
        result = cursor.fetchone()
        conn.close()

        if result:
            last_act = result['last_activity']
            current_time = result['current_time']
            return {
                "last_activity": last_act,
                "current_time": current_time
            }
        return None
    
    def get_general_stats(self) -> Dict:
        """Получение общей статистики"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Всего пользователей
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        total_users = cursor.fetchone()['cnt']
        
        # Активные сегодня
        today = date.today()
        cursor.execute("""
            SELECT COUNT(*) as cnt FROM users 
            WHERE DATE(last_activity) = ?
        """, (today,))
        active_today = cursor.fetchone()['cnt']
        
        # Новые за неделю
        week_ago = datetime.now() - timedelta(days=7)
        cursor.execute("""
            SELECT COUNT(*) as cnt FROM users 
            WHERE registration_date >= ?
        """, (week_ago,))
        new_week = cursor.fetchone()['cnt']
        
        # С активной подпиской
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) as cnt 
            FROM subscriptions 
            WHERE is_active = 1 AND end_date > ?
        """, (datetime.now(),))
        with_subscription = cursor.fetchone()['cnt']
        
        # Всего сообщений
        cursor.execute("SELECT SUM(message_count) as cnt FROM users")
        total_messages = cursor.fetchone()['cnt'] or 0
        
        # Сообщений сегодня
        cursor.execute("""
            SELECT COUNT(*) as cnt FROM message_history 
            WHERE DATE(timestamp) = ?
        """, (today,))
        today_messages = cursor.fetchone()['cnt']
        
        # Токены и затраты
        cursor.execute("""
            SELECT 
                SUM(total_input_tokens) as input_tokens,
                SUM(total_output_tokens) as output_tokens,
                SUM(total_cost) as cost
            FROM users
        """)
        tokens_data = cursor.fetchone()
        
        conn.close()
        
        return {
            "total_users": total_users,
            "active_today": active_today,
            "new_week": new_week,
            "with_subscription": with_subscription,
            "total_messages": total_messages,
            "today_messages": today_messages,
            "total_input_tokens": tokens_data['input_tokens'] or 0,
            "total_output_tokens": tokens_data['output_tokens'] or 0,
            "total_cost": tokens_data['cost'] or 0.0
        }
    
    def get_recent_users(self, limit: int = 10) -> List[Dict]:
        """Получение последних пользователей"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                u.user_id,
                u.username,
                u.full_name,
                u.registration_date,
                u.message_count,
                CASE WHEN s.user_id IS NOT NULL THEN 1 ELSE 0 END as has_subscription
            FROM users u
            LEFT JOIN (
                SELECT DISTINCT user_id 
                FROM subscriptions 
                WHERE is_active = 1 AND end_date > ?
            ) s ON u.user_id = s.user_id
            ORDER BY u.registration_date DESC
            LIMIT ?
        """, (datetime.now(), limit))
        
        results = cursor.fetchall()
        conn.close()
        
        return [
            {
                "user_id": row['user_id'],
                "username": row['username'] or "Нет username",
                "full_name": row['full_name'],
                "registration_date": datetime.strptime(row['registration_date'], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y'),
                "message_count": row['message_count'],
                "has_subscription": bool(row['has_subscription'])
            }
            for row in results
        ]
    
    def get_finance_stats(self) -> Dict:
        """Получение финансовой статистики"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Доход от подписок
        cursor.execute("SELECT SUM(price) as total FROM subscriptions")
        total_revenue = cursor.fetchone()['total'] or 0
        
        # За месяц
        month_ago = datetime.now() - timedelta(days=30)
        cursor.execute("""
            SELECT SUM(price) as total 
            FROM subscriptions 
            WHERE start_date >= ?
        """, (month_ago,))
        month_revenue = cursor.fetchone()['total'] or 0
        
        # За неделю
        week_ago = datetime.now() - timedelta(days=7)
        cursor.execute("""
            SELECT SUM(price) as total 
            FROM subscriptions 
            WHERE start_date >= ?
        """, (week_ago,))
        week_revenue = cursor.fetchone()['total'] or 0
        
        # Расходы на API
        cursor.execute("SELECT SUM(total_cost) as cost FROM users")
        total_api_cost = cursor.fetchone()['cost'] or 0.0
        
        # За месяц (примерная оценка по сообщениям за последний месяц)
        cursor.execute("""
            SELECT SUM(u.total_cost) / u.message_count * COUNT(m.id) as cost
            FROM users u
            JOIN message_history m ON u.user_id = m.user_id
            WHERE m.timestamp >= ?
        """, (month_ago,))
        month_api_cost = cursor.fetchone()['cost'] or 0.0
        
        # За неделю
        cursor.execute("""
            SELECT SUM(u.total_cost) / u.message_count * COUNT(m.id) as cost
            FROM users u
            JOIN message_history m ON u.user_id = m.user_id
            WHERE m.timestamp >= ?
        """, (week_ago,))
        week_api_cost = cursor.fetchone()['cost'] or 0.0
        
        # Активные подписки
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) as cnt 
            FROM subscriptions 
            WHERE is_active = 1 AND end_date > ?
        """, (datetime.now(),))
        active_subscriptions = cursor.fetchone()['cnt']
        
        # Всего продано
        cursor.execute("SELECT COUNT(*) as cnt FROM subscriptions")
        total_subscriptions = cursor.fetchone()['cnt']
        
        conn.close()
        
        return {
            "total_revenue": total_revenue,
            "month_revenue": month_revenue,
            "week_revenue": week_revenue,
            "total_api_cost": total_api_cost,
            "month_api_cost": month_api_cost,
            "week_api_cost": week_api_cost,
            "active_subscriptions": active_subscriptions,
            "total_subscriptions": total_subscriptions
        }
    
    def get_top_users(self, limit: int = 10) -> List[Dict]:
        """Получение топа пользователей"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT user_id, username, full_name, message_count, total_cost
            FROM users
            ORDER BY message_count DESC
            LIMIT ?
        """, (limit,))
        
        results = cursor.fetchall()
        conn.close()
        
        return [
            {
                "user_id": row['user_id'],
                "username": row['username'] or "Нет username",
                "full_name": row['full_name'],
                "message_count": row['message_count'],
                "total_cost": row['total_cost']
            }
            for row in results
        ]
    
    def get_all_user_ids(self) -> List[int]:
        """Получение ID всех пользователей"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM users")
        results = cursor.fetchall()
        conn.close()
        
        return [row['user_id'] for row in results]