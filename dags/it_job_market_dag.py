from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

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
            "exec python pipeline/main.py --source itviec --limit 5"
        ),
    )

    run_topdev_pipeline = BashOperator(
        task_id="run_topdev_pipeline",
        bash_command=(
            "cd /opt/airflow/project && "
            "exec python pipeline/main.py --source topdev --limit 10"
        ),
    )

    run_itviec_pipeline >> run_topdev_pipeline
