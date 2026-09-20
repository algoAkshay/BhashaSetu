from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.models import Scheme


class SchemeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, scheme_id: int) -> Scheme | None:
        return self.session.scalar(select(Scheme).where(Scheme.id == scheme_id)
                                   .options(selectinload(Scheme.rules)).execution_options(populate_existing=True))

    def get_by_source_key(self, source_key: str) -> Scheme | None:
        return self.session.scalar(select(Scheme).where(Scheme.source_key == source_key))

    def get_active_schemes(self) -> list[Scheme]:
        return list(self.session.scalars(select(Scheme).where(Scheme.is_active.is_(True))
                                        .options(selectinload(Scheme.rules)).order_by(Scheme.id)
                                        .execution_options(populate_existing=True)))

    def create(self, *, name: str, source_key: str | None = None, **metadata) -> Scheme:
        scheme = Scheme(name=name, source_key=source_key, **metadata)
        self.session.add(scheme)
        self.session.flush()
        return scheme

    def update(self, scheme: Scheme, **changes) -> Scheme:
        from backend.admin_schemas import SchemePatch
        allowed = set(SchemePatch.model_fields)
        if changes.keys() - allowed:
            raise ValueError("Unsupported scheme update field.")
        for key, value in changes.items():
            setattr(scheme, key, value)
        self.session.flush()
        return scheme

    def set_active(self, scheme: Scheme, is_active: bool) -> Scheme:
        if type(is_active) is not bool:
            raise ValueError("is_active must be boolean.")
        return self.update(scheme, is_active=is_active)

    def admin_page(self, *, search, government_level, state_or_ut, status_confidence,
                   category, is_active, page, page_size):
        query = select(Scheme)
        if search:
            # Escape SQL wildcards so a name search is a literal substring.
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(Scheme.name.ilike(f"%{escaped}%", escape="\\"))
        for column, value in ((Scheme.government_level, government_level), (Scheme.state, state_or_ut),
                              (Scheme.status_confidence, status_confidence), (Scheme.category, category),
                              (Scheme.is_active, is_active)):
            if value is not None:
                query = query.where(column == value)
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        items = list(self.session.scalars(query.options(selectinload(Scheme.rules))
                     .order_by(Scheme.name, Scheme.id).offset((page - 1) * page_size).limit(page_size)))
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def admin_filter_options(self):
        return {key: list(self.session.scalars(select(column).where(column.is_not(None), column != "")
                      .distinct().order_by(column)))
                for key, column in (("state_or_ut", Scheme.state), ("category", Scheme.category))}
