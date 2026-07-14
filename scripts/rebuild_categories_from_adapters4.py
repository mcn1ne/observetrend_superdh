"""저장된 adapters4 결과로 운영 카테고리를 일괄 재구성한다."""
from app.config import settings
from app.services.categorize import rebuild_categories_from_analyses


if __name__ == "__main__":
    for game in settings.games:
        print(rebuild_categories_from_analyses(game.id))
