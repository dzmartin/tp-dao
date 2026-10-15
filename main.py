from container import create_application
from ui.menu import run_menu


def main() -> None:
	application = create_application()
	try:
		run_menu(application)
	finally:
		application.close()


if __name__ == "__main__":
	main()
