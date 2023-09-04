import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job

## @params: [JOB_NAME]
args = getResolvedOptions(sys.argv, ['JOB_NAME', 'iceberg_job_catalog_warehouse'])
conf = SparkConf()

## Please make sure to pass runtime argument --iceberg_job_catalog_warehouse with value as the S3 path 
conf.set("spark.sql.catalog.job_catalog.warehouse", args['iceberg_job_catalog_warehouse'])
conf.set("spark.sql.catalog.job_catalog", "org.apache.iceberg.spark.SparkCatalog")
conf.set("spark.sql.catalog.job_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
conf.set("spark.sql.catalog.job_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
conf.set("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
conf.set("spark.sql.source.partitionOverwriteMode", "dynamic")
conf.set("spark.sql.iceberg.handle-timestamp-without-timezone", "true")

sc = SparkContext(conf=conf)
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

## Read Input Table
IncrementalInputDyF = glueContext.create_dynamic_frame.from_catalog(database = "iceberg_demo", table_name = "raw_csv_input", transformation_ctx = "IncrementalInputDyF")
IncrementalInputDF = IncrementalInputDyF.toDF()

if not IncrementalInputDF.rdd.isEmpty():
    ## Apply De-duplication logic on input data, to pickup latest record based on timestamp and operation
    IDWindowDF = Window.partitionBy(IncrementalInputDF.product_id).orderBy(IncrementalInputDF.last_update_time).rangeBetween(-sys.maxsize, sys.maxsize)
    
    inputDFWithTS = IncrementalInputDF.withColumn("max_op_date", max(IncrementalInputDF.last_update_time).over(IDWindowDF))
    
    NewInsertsDF = inputDFWithTS.filter("last_update_time=max_op_date").filter("op='I")
    UpdateDeleteDf = inputDFWithTS.filter("last_update_time=max_op_date").filter("op IN ('U', 'D')")
    finalInputDF = NewInsertsDF.unionAll(UpdateDeleteDf)

    finalInputDF.createOrReplaceTempView("incremental_input_data")
    finalInputDF.show()
    
    IcebergMergeOutputDF = spark.sql("""
    MERGE INTO job_catalog.iceberg_demo.iceberg_output t
    USING (SELECT op, product_id, category, product_name, quantity_available, to_timestamp(last_update_time) as last_update_time FROM incremental_input_data) s
    ON t.product_id = s.product_id
    WHEN MATCHED AND s.op = 'D' THEN DELETE
    WHEN MATCHED THEN UPDATE SET t.quantity_available = s.quantity_available, t.last_update_time = s.last_update_time 
    WHEN NOT MATCHED THEN INSERT (product_id, category, product_name, quantity_available, last_update_time) VALUES (s.product_id, s.category, s.product_name, s.quantity_available, s.last_update_time)
    """)f
    
    job.commit()