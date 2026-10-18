import argparse
import getpass
import re
import sys

from psycopg.errors import UniqueViolation

from marketplace.auth import hash_password
from marketplace.database import connect


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать администратора маркетплейса")
    parser.add_argument("username")
    parser.add_argument("--password-stdin", action="store_true", help="Прочитать пароль из stdin вместо интерактивного ввода")
    arguments = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", arguments.username):
        parser.error("Имя должно содержать 3–64 латинских буквы, цифры или подчёркивания")
    password = sys.stdin.readline().rstrip("\r\n") if arguments.password_stdin else getpass.getpass("Пароль: ")
    if not 8 <= len(password) <= 128:
        parser.error("Длина пароля должна быть от 8 до 128 символов")
    try:
        with connect() as connection:
            connection.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'ADMIN')", (arguments.username, hash_password(password)))
    except UniqueViolation:
        parser.exit(1, "Пользователь с таким именем уже существует\n")
    print(f"Администратор {arguments.username} создан")


if __name__ == "__main__":
    main()
