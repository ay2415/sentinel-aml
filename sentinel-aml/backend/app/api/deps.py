from app.database.session import get_db  # noqa: F401  (re-export for routes)


def paginate(page: int, page_size: int) -> tuple[int, int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    return (page - 1) * page_size, page_size
