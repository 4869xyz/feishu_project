"""Business services used by the Feishu listener."""

from services.download_cache import CacheCleanupResult, DownloadCacheCleaner

from services.sales_workbook_aggregator import (
    AggregationResult,
    DuplicateSourceError,
    SalesAggregationError,
    SourceValidationResult,
    SourceValidationError,
    SourceWorkbook,
    TemplateValidationError,
    aggregate_sales_workbooks,
    validate_source_workbook,
)

__all__ = [
    "CacheCleanupResult",
    "DownloadCacheCleaner",
    "AggregationResult",
    "DuplicateSourceError",
    "SalesAggregationError",
    "SourceValidationResult",
    "SourceValidationError",
    "SourceWorkbook",
    "TemplateValidationError",
    "aggregate_sales_workbooks",
    "validate_source_workbook",
]
