from dataclasses import dataclass

from config import DATABASE_PATH
from repositories.sqlite_repository import SQLiteRepository
from services.auth_service import AuthService
from services.report_service import ReportService
from services.retail_service import RetailService


@dataclass
class Application:
	repository: SQLiteRepository
	auth: AuthService
	retail: RetailService
	reports: ReportService

	def close(self) -> None:
		self.repository.close()


def create_application(database_path: str = str(DATABASE_PATH)) -> Application:
	repository = SQLiteRepository(database_path)
	return Application(repository, AuthService(repository), RetailService(repository), ReportService(repository))
