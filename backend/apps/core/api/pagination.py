"""
Pagination.

Offset pagination is wrong for the screens that matter here. A front desk
watching the live queue, or a ward scrolling a long patient list, gets
duplicated and skipped rows whenever something is inserted between page
requests — and in a hospital something is always being inserted.

So: page numbers for small, stable lists (staff, roles, price lists), and
cursor pagination for anything that grows while you read it.
"""

from __future__ import annotations

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class CuraviaPagination(PageNumberPagination):
    """The default. Explicit counts, because clerks want to know how many."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200

    def get_paginated_response(self, data) -> Response:  # type: ignore[no-untyped-def]
        # paginate_queryset() always runs first, so both are set by now; the
        # stubs type them Optional because they are None before that call.
        assert self.page is not None
        assert self.request is not None
        return Response(
            {
                "count": self.page.paginator.count,
                "page": self.page.number,
                "pages": self.page.paginator.num_pages,
                "page_size": self.get_page_size(self.request),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )


class TimelinePagination(CursorPagination):
    """
    For feeds that change while you are looking at them: the audit log, the
    patient timeline, the journey board, notifications.
    """

    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"
    ordering = "-created_at"


class AuditPagination(TimelinePagination):
    # CursorPagination accepts a tuple at runtime; the stubs declare `str`.
    ordering = ("-at", "-sequence")  # type: ignore[assignment]
