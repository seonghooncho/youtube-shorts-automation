output "bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
}

output "content_table_name" {
  value = aws_dynamodb_table.content.name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "codebuild_project_name" {
  value = aws_codebuild_project.image.name
}

output "batch_job_queue" {
  value = aws_batch_job_queue.pipeline.name
}

output "batch_job_definition" {
  value = aws_batch_job_definition.stage.name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "publisher_function_name" {
  value = aws_lambda_function.publisher.function_name
}

output "generate_schedule_name" {
  value = aws_scheduler_schedule.generate.name
}

output "upload_schedule_name" {
  value = aws_scheduler_schedule.upload.name
}
