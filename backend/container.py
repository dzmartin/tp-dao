from dataclasses import dataclass

from backend.config import DATABASE_PATH
from backend.persistencia.sqlite_repository import SQLiteRepository
from backend.servicios.auth_service import AuthService
from backend.servicios.report_service import ReportService
from backend.servicios.retail_service import RetailService


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
