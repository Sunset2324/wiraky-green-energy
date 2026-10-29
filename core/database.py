import mysql.connector
from config.settings import Config

def get_db_connection():
    try:
        connection = mysql.connector.connect(
            host=Config.DB_HOST,
            user=Config.DB_USER,
            password=Config.DB_PASS,
            database=Config.DB_NAME
        )
        return connection
    except mysql.connector.Error as err:
        print(f"Database connection error: {err}")
        return None