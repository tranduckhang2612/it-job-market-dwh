-- SQL Server. Read-only examples; no sample rows are inserted.
SET NOCOUNT ON;

-- FactJob: change this to a real job_id in your DW. Returns at most one row.
DECLARE @job_id nvarchar(256) = N'topdev:1';

SELECT CONCAT(N'Vào ngày ', CONVERT(char(10), d.calendar_date, 103),
              N' (observed_date_key = ', f.observed_date_key, N'), nguồn ', f.source,
              N' (source) ghi nhận tin tuyển dụng ', COALESCE(f.title, N'(chưa rõ tiêu đề)'),
              N' của công ty ', COALESCE(f.company_name, N'(chưa rõ công ty)'),
              N' (fact_job_key = ', f.fact_job_key, N'), với số tin là ',
              f.job_posting_count, N' (job_posting_count).') AS fact_job_example
FROM dw.fact_job f
JOIN dw.dim_date d ON d.date_key = f.observed_date_key
WHERE f.job_id = @job_id;

-- FactInternetSales: one illustrative sales row, not a table in the job DW.
SELECT CONCAT(N'Vào ngày ',
              CONVERT(char(10), CONVERT(date, CONVERT(char(8), OrderDateKey), 112), 103),
              N' (OrderDateKey), khách hàng ', CustomerKey,
              N' (CustomerKey) đã mua sản phẩm ', ProductName,
              N' có mã ', ProductKey, N' (ProductKey) với tổng số tiền giao dịch là ',
              FORMAT(SalesAmount, 'C2', 'en-US'), N' (SalesAmount).') AS fact_internet_sales_example
FROM (VALUES (20201229, 21768, 310, N'Adjustable Race', CAST(3578.27 AS decimal(12,2))))
    sale(OrderDateKey, CustomerKey, ProductKey, ProductName, SalesAmount);
