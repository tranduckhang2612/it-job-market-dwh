#!/usr/bin/env python3
"""Entry point: crawl ITviec vào PostgreSQL rồi chuẩn hóa đúng lần crawl đó.

Python >= 3.10. Cài dependency trong requirements.txt trước khi chạy.
Chạy từ thư mục gốc project (hoặc đặt cả ba file Python cùng thư mục):
    python main_itviec.py --cookie-file data_preprocessing/itviec_crawl_data/cookie_itviec.txt --limit 20

Bước 1 lưu JSON gốc vào raw.job_postings, ghi tiến độ vào raw.crawl_runs.
Bước 2 đọc đúng crawl_run_id vừa tạo và ghi vào các bảng normalized.
Mặc định không xuất JSON/CSV ra đĩa. Các tùy chọn --db-* áp dụng cho cả hai bước.
"""

from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path

from data_preprocessing.itviec_crawl_data import crawl_itviec
from data_preprocessing.itviec_normalize_data import normalize_itviec


LOG = logging.getLogger("itviec.pipeline")


def main(argv=None) -> int:
    args = crawl_itviec.parse_args(argv, description=__doc__)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args.crawl_run_id = crawl_itviec.new_crawl_run_id()
    args.db_password = None
    try:
        # Prompt once, reuse only in memory, and never include it in crawl metadata.
        if args.db_password_prompt:
            try:
                args.db_password = getpass.getpass("Mật khẩu PostgreSQL: ")
            except EOFError:
                LOG.error("Không nhập được mật khẩu PostgreSQL; dùng terminal tương tác hoặc cấu hình .pgpass.")
                return 4
            args.db_password_prompt = False
        LOG.info("Bước 1/2: crawl dữ liệu gốc vào PostgreSQL.")
        crawl_status = crawl_itviec.run(args)
        result = getattr(args, "crawl_result", None)
        if crawl_status == 4 or (isinstance(result, dict) and result.get("storage_failed")):
            return 4
        if not isinstance(result, dict) or result.get("crawl_run_id") != args.crawl_run_id:
            LOG.error("Không xác nhận được kết quả của lần crawl vừa chạy; dừng trước bước chuẩn hóa.")
            return crawl_status or 1
        if result.get("interrupted"):
            return 130
        collected_count = result.get("collected_count", 0)
        if not isinstance(collected_count, int) or collected_count <= 0:
            LOG.error("Lần crawl vừa chạy không có tin; dừng trước bước chuẩn hóa.")
            return crawl_status or 1
        LOG.info("Bước 2/2: chuẩn hóa %s tin từ PostgreSQL; crawl_run_id=%s.",
                 collected_count, args.crawl_run_id)
        normalize_status = normalize_itviec.normalize_database_run(args.crawl_run_id, args)
        if normalize_status == 4:
            return 4
        if normalize_status:
            LOG.error("Bước chuẩn hóa chưa hoàn thành; xem raw.crawl_runs.metadata.normalization.")
        return crawl_status if crawl_status else normalize_status
    except KeyboardInterrupt:
        return 130
    finally:
        args.db_password = None


if __name__ == "__main__":
    sys.exit(main())
