from datetime import datetime, timedelta
import os

from airflow import DAG
from airflow.operators.bash import BashOperator


PIPELINE_MODE = os.environ.get("DWH_PIPELINE_MODE", "shadow").strip().lower()
if PIPELINE_MODE not in {"shadow", "direct"}:
    raise ValueError("DWH_PIPELINE_MODE must be 'shadow' or 'direct'")

# Shadow keeps the legacy dw snapshot current for rollback. Direct makes
# normalize_data -> dw_v2 the only warehouse write path after consumer cutover.
NORMALIZE_ONLY = " --normalize-only" if PIPELINE_MODE == "direct" else ""

default_args = {
    "owner": "data_engineer",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="it_job_market_etl_daily",
    default_args=default_args,
    description="Tự động crawl ITviec & TopDev, chuẩn hóa và nạp SQL Server DWH",
    schedule="0 1 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["etl", "dwh", "it-jobs"],
) as dag:

    run_itviec_pipeline = BashOperator(
        task_id="run_itviec_pipeline",
        bash_command=(
            "cd /opt/airflow/project && "
            f"exec python pipeline/main.py --source itviec --limit 5{NORMALIZE_ONLY}"
        ),
    )

    run_topdev_pipeline = BashOperator(
        task_id="run_topdev_pipeline",
        bash_command=(
            "cd /opt/airflow/project && "
            f"exec python pipeline/main.py --source topdev --limit 10{NORMALIZE_ONLY}"
        ),
    )

    load_dw_v2_direct = BashOperator(
        task_id="load_dw_v2_direct",
        bash_command=(
            "cd /opt/airflow/project && "
            "exec python load.py warehouse-v2-direct --apply"
        ),
    )

    publish_dw_v2 = BashOperator(
        task_id="publish_dw_v2",
        bash_command=(
            "cd /opt/airflow/project && "
            f"exec python load.py warehouse-v2 --apply --mode {PIPELINE_MODE}"
        ),
    )

    run_itviec_pipeline >> run_topdev_pipeline >> load_dw_v2_direct >> publish_dw_v2
