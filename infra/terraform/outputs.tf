output "bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
}

output "launcher_function_name" {
  value = aws_lambda_function.launcher.function_name
}

output "generate_rule_name" {
  value = aws_cloudwatch_event_rule.generate.name
}

output "upload_rule_name" {
  value = aws_cloudwatch_event_rule.upload.name
}

output "job_instance_profile" {
  value = aws_iam_instance_profile.job.name
}
