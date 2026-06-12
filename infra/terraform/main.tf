data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  tags = {
    Project   = "youtube-shorts-automation"
    ManagedBy = "terraform"
  }

  ssm_parameter_prefix = "/ytshorts"

  batch_secret_names = [
    "OPENAI_API_KEY",
    "HF_TOKEN",
    "PIXABAY_API_KEY",
    "SLACK_WEBHOOK_URL",
  ]

  batch_secrets = [
    for name in local.batch_secret_names : {
      name      = name
      valueFrom = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_parameter_prefix}/${name}"
    }
  ]

  batch_environment = [
    { name = "AWS_REGION", value = var.aws_region },
    { name = "AWS_DEFAULT_REGION", value = var.aws_region },
    { name = "S3_BUCKET_NAME", value = aws_s3_bucket.artifacts.bucket },
    { name = "CONTENT_TABLE_NAME", value = aws_dynamodb_table.content.name },
    { name = "TARGET_PLATFORMS", value = "youtube" },
    { name = "YOUTUBE_PRIVACY_STATUS", value = var.youtube_privacy_status },
    { name = "REDDIT_MAX_POSTS", value = tostring(var.reddit_max_posts) },
    { name = "REDDIT_MIN_NEEDED", value = tostring(var.reddit_min_needed) },
    { name = "REDDIT_FALLBACK_PROVIDER", value = "pullpush" },
    { name = "GENERATION_BATCH_DAYS", value = tostring(var.generation_batch_days) },
    { name = "GENERATION_BUFFER_DAYS", value = tostring(var.generation_buffer_days) },
    { name = "GENERATION_MAX_NEW_ITEMS", value = tostring(var.generation_max_new_items) },
    { name = "SCHEDULE_TIMEZONE", value = var.schedule_timezone },
    { name = "PUBLISH_HOUR_LOCAL", value = tostring(var.publish_hour_local) },
    { name = "PUBLISH_MINUTE_LOCAL", value = tostring(var.publish_minute_local) },
    { name = "PUBLISH_REBASE_STALE_DAYS", value = tostring(var.publish_rebase_stale_days) },
    { name = "FILTER_MODEL", value = var.openai_filter_model },
    { name = "SCRIPT_MODEL", value = var.openai_script_model },
  ]
}

resource "aws_s3_bucket" "artifacts" {
  bucket = var.bucket_name
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-raw-artifacts"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "expire-script-artifacts"
    status = "Enabled"

    filter {
      prefix = "scripts/"
    }

    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "expire-audio-artifacts"
    status = "Enabled"

    filter {
      prefix = "audio/"
    }

    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "expire-video-source-artifacts"
    status = "Enabled"

    filter {
      prefix = "videos/sources/"
    }

    expiration {
      days = 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "expire-old-final-videos"
    status = "Enabled"

    filter {
      prefix = "videos/final/"
    }

    expiration {
      days = 180
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {
      prefix = ""
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_dynamodb_table" "content" {
  name         = "${var.project_name}-content"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "content_id"

  attribute {
    name = "content_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "scheduled_publish_at"
    type = "N"
  }

  global_secondary_index {
    name            = "status-schedule-index"
    hash_key        = "status"
    range_key       = "scheduled_publish_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.tags
}

resource "aws_ecr_repository" "app" {
  name                 = "${var.project_name}-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the latest 5 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "aws_security_group" "job" {
  name        = "${var.project_name}-job"
  description = "Outbound-only security group for youtube shorts automation jobs"
  vpc_id      = data.aws_vpc.default.id
  tags        = local.tags
}

resource "aws_vpc_security_group_egress_rule" "job_all" {
  security_group_id = aws_security_group.job.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_cloudwatch_log_group" "batch" {
  name              = "/aws/batch/${var.project_name}"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/aws/codebuild/${var.project_name}-image-build"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "publisher" {
  name              = "/aws/lambda/${var.project_name}-publisher"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "legacy_launcher" {
  name              = "/aws/lambda/${var.project_name}-launcher"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "budget_notifier" {
  name              = "/aws/lambda/${var.project_name}-budget-notifier"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_iam_role" "codebuild" {
  name = "${var.project_name}-codebuild-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "codebuild.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "codebuild" {
  name = "${var.project_name}-codebuild-policy"
  role = aws_iam_role.codebuild.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.artifacts.arn}/${var.source_bundle_key}"
      },
      {
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeRepositories",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ]
        Resource = aws_ecr_repository.app.arn
      }
    ]
  })
}

resource "aws_codebuild_project" "image" {
  name          = "${var.project_name}-image-build"
  description   = "Build and publish the youtube shorts automation Batch image"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 60
  tags          = local.tags

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type    = var.codebuild_compute_type
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = var.aws_region
    }

    environment_variable {
      name  = "IMAGE_REPO_NAME"
      value = aws_ecr_repository.app.name
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.artifacts.bucket}/${var.source_bundle_key}"
    buildspec = "buildspec.yml"
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.codebuild.name
      status     = "ENABLED"
    }
  }
}

resource "aws_iam_role" "batch_service" {
  name = "${var.project_name}-batch-service-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "batch.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "batch_service" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

resource "aws_iam_role" "batch_execution" {
  name = "${var.project_name}-batch-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "batch_execution" {
  role       = aws_iam_role.batch_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "batch_execution_secrets" {
  name = "${var.project_name}-batch-execution-secrets"
  role = aws_iam_role.batch_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = [
          for name in local.batch_secret_names :
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_parameter_prefix}/${name}"
        ]
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role" "batch_job" {
  name = "${var.project_name}-batch-job-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "batch_job" {
  name = "${var.project_name}-batch-job-policy"
  role = aws_iam_role.batch_job.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.artifacts.arn,
          "${aws_s3_bucket.artifacts.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:BatchWriteItem",
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.content.arn,
          "${aws_dynamodb_table.content.arn}/index/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "polly:SynthesizeSpeech",
          "polly:DescribeVoices"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_parameter_prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

resource "aws_batch_compute_environment" "fargate" {
  compute_environment_name = "${var.project_name}-fargate"
  type                     = "MANAGED"
  state                    = "ENABLED"
  service_role             = aws_iam_role.batch_service.arn

  compute_resources {
    type               = "FARGATE"
    max_vcpus          = var.batch_max_vcpus
    subnets            = data.aws_subnets.default_public.ids
    security_group_ids = [aws_security_group.job.id]
  }

  depends_on = [aws_iam_role_policy_attachment.batch_service]
  tags       = local.tags
}

resource "aws_batch_job_queue" "pipeline" {
  name     = "${var.project_name}-pipeline"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.fargate.arn
  }

  tags = local.tags
}

resource "aws_batch_job_definition" "stage" {
  name                  = "${var.project_name}-stage"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.app.repository_url}:latest"
    command          = ["python", "runner.py"]
    executionRoleArn = aws_iam_role.batch_execution.arn
    jobRoleArn       = aws_iam_role.batch_job.arn
    environment      = local.batch_environment
    secrets          = local.batch_secrets
    resourceRequirements = [
      { type = "VCPU", value = var.batch_light_vcpu },
      { type = "MEMORY", value = var.batch_light_memory_mib }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.batch.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "stage"
      }
    }
    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }
  })

  retry_strategy {
    attempts = 1
  }

  timeout {
    attempt_duration_seconds = var.batch_timeout_seconds
  }

  tags = local.tags
}

resource "aws_batch_job_definition" "script" {
  name                  = "${var.project_name}-script"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.app.repository_url}:latest"
    command          = ["python", "runner.py"]
    executionRoleArn = aws_iam_role.batch_execution.arn
    jobRoleArn       = aws_iam_role.batch_job.arn
    environment      = local.batch_environment
    secrets          = local.batch_secrets
    resourceRequirements = [
      { type = "VCPU", value = var.batch_script_vcpu },
      { type = "MEMORY", value = var.batch_script_memory_mib }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.batch.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "script"
      }
    }
    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }
  })

  retry_strategy {
    attempts = 1
  }

  timeout {
    attempt_duration_seconds = var.batch_timeout_seconds
  }

  tags = local.tags
}

resource "aws_batch_job_definition" "render" {
  name                  = "${var.project_name}-render"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.app.repository_url}:latest"
    command          = ["python", "runner.py"]
    executionRoleArn = aws_iam_role.batch_execution.arn
    jobRoleArn       = aws_iam_role.batch_job.arn
    environment      = local.batch_environment
    secrets          = local.batch_secrets
    resourceRequirements = [
      { type = "VCPU", value = var.batch_render_vcpu },
      { type = "MEMORY", value = var.batch_render_memory_mib }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.batch.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "render"
      }
    }
    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }
  })

  retry_strategy {
    attempts = 1
  }

  timeout {
    attempt_duration_seconds = var.batch_timeout_seconds
  }

  tags = local.tags
}

resource "aws_iam_role" "publisher" {
  name = "${var.project_name}-publisher-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "publisher_basic" {
  role       = aws_iam_role.publisher.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "publisher" {
  name = "${var.project_name}-publisher-policy"
  role = aws_iam_role.publisher.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.artifacts.arn,
          "${aws_s3_bucket.artifacts.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.content.arn,
          "${aws_dynamodb_table.content.arn}/index/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_parameter_prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

data "archive_file" "publisher" {
  type        = "zip"
  source_file = "${path.module}/lambda/publisher.py"
  output_path = "${path.module}/.build/publisher.zip"
}

resource "aws_lambda_function" "publisher" {
  function_name    = "${var.project_name}-publisher"
  role             = aws_iam_role.publisher.arn
  handler          = "publisher.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.publisher.output_path
  source_code_hash = data.archive_file.publisher.output_base64sha256
  timeout          = 900
  memory_size      = 1024

  ephemeral_storage {
    size = 2048
  }

  environment {
    variables = {
      BUCKET_NAME               = aws_s3_bucket.artifacts.bucket
      CONTENT_TABLE_NAME        = aws_dynamodb_table.content.name
      SSM_PARAMETER_PREFIX      = local.ssm_parameter_prefix
      YOUTUBE_PRIVACY_STATUS    = var.youtube_privacy_status
      SCHEDULE_TIMEZONE         = var.schedule_timezone
      PUBLISH_HOUR_LOCAL        = tostring(var.publish_hour_local)
      PUBLISH_MINUTE_LOCAL      = tostring(var.publish_minute_local)
      PUBLISH_REBASE_STALE_DAYS = tostring(var.publish_rebase_stale_days)
    }
  }

  depends_on = [aws_cloudwatch_log_group.publisher]

  tags = local.tags
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
  tags = local.tags
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = [
            "events.amazonaws.com",
            "budgets.amazonaws.com"
          ]
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.alerts.arn
      }
    ]
  })
}

resource "aws_iam_role" "budget_notifier" {
  name = "${var.project_name}-budget-notifier-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "budget_notifier_basic" {
  role       = aws_iam_role.budget_notifier.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "budget_notifier" {
  name = "${var.project_name}-budget-notifier-policy"
  role = aws_iam_role.budget_notifier.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_parameter_prefix}/SLACK_WEBHOOK_URL"
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

data "archive_file" "budget_notifier" {
  type        = "zip"
  source_file = "${path.module}/lambda/budget_notifier.py"
  output_path = "${path.module}/.build/budget_notifier.zip"
}

resource "aws_lambda_function" "budget_notifier" {
  function_name    = "${var.project_name}-budget-notifier"
  role             = aws_iam_role.budget_notifier.arn
  handler          = "budget_notifier.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.budget_notifier.output_path
  source_code_hash = data.archive_file.budget_notifier.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      SSM_PARAMETER_PREFIX = local.ssm_parameter_prefix
    }
  }

  depends_on = [aws_cloudwatch_log_group.budget_notifier]
  tags       = local.tags
}

resource "aws_sns_topic_subscription" "alerts_to_budget_notifier" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.budget_notifier.arn
}

resource "aws_lambda_permission" "allow_sns_alerts" {
  statement_id  = "AllowExecutionFromSnsAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.budget_notifier.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alerts.arn
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project_name}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.alerts]
}

resource "aws_iam_role" "step_functions" {
  name = "${var.project_name}-sfn-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "states.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "step_functions" {
  name = "${var.project_name}-sfn-policy"
  role = aws_iam_role.step_functions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "batch:SubmitJob"
        ]
        Resource = [
          aws_batch_job_queue.pipeline.arn,
          aws_batch_job_definition.stage.arn,
          aws_batch_job_definition.script.arn,
          aws_batch_job_definition.render.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "batch:DescribeJobs",
          "batch:TerminateJob"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "events:DescribeRule",
          "events:PutRule",
          "events:PutTargets"
        ]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForBatchJobsRule"
      },
      {
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          aws_iam_role.batch_execution.arn,
          aws_iam_role.batch_job.arn
        ]
      },
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = [aws_lambda_function.publisher.arn, "${aws_lambda_function.publisher.arn}:*"]
      }
    ]
  })
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.project_name}-pipeline"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Generate and publish YouTube Shorts"
    StartAt = "ChooseWorkflow"
    States = {
      ChooseWorkflow = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.mode"
            StringEquals = "upload"
            Next         = "PublishReady"
          },
          {
            Variable           = "$.days"
            NumericGreaterThan = 0
            Next               = "Collect"
          }
        ]
        Default = "SetDefaultGenerationDays"
      }
      SetDefaultGenerationDays = {
        Type       = "Pass"
        Result     = var.generation_batch_days
        ResultPath = "$.days"
        Next       = "Collect"
      }
      Collect = {
        Type       = "Task"
        Resource   = "arn:aws:states:::batch:submitJob.sync"
        ResultPath = null
        Parameters = {
          JobName       = "${var.project_name}-collect"
          JobQueue      = aws_batch_job_queue.pipeline.arn
          JobDefinition = aws_batch_job_definition.stage.arn
          ContainerOverrides = {
            Environment = [
              { Name = "STAGE", Value = "collect" },
              { Name = "GENERATION_BATCH_DAYS", "Value.$" = "States.Format('{}', $.days)" }
            ]
          }
        }
        Next = "Filter"
      }
      Filter = {
        Type       = "Task"
        Resource   = "arn:aws:states:::batch:submitJob.sync"
        ResultPath = null
        Parameters = {
          JobName       = "${var.project_name}-filter"
          JobQueue      = aws_batch_job_queue.pipeline.arn
          JobDefinition = aws_batch_job_definition.stage.arn
          ContainerOverrides = {
            Environment = [
              { Name = "STAGE", Value = "filter" },
              { Name = "GENERATION_BATCH_DAYS", "Value.$" = "States.Format('{}', $.days)" }
            ]
          }
        }
        Next = "Script"
      }
      Script = {
        Type       = "Task"
        Resource   = "arn:aws:states:::batch:submitJob.sync"
        ResultPath = null
        Parameters = {
          JobName       = "${var.project_name}-script"
          JobQueue      = aws_batch_job_queue.pipeline.arn
          JobDefinition = aws_batch_job_definition.script.arn
          ContainerOverrides = {
            Environment = [
              { Name = "STAGE", Value = "script" },
              { Name = "GENERATION_BATCH_DAYS", "Value.$" = "States.Format('{}', $.days)" }
            ]
          }
        }
        Next = "Tts"
      }
      Tts = {
        Type       = "Task"
        Resource   = "arn:aws:states:::batch:submitJob.sync"
        ResultPath = null
        Parameters = {
          JobName       = "${var.project_name}-tts"
          JobQueue      = aws_batch_job_queue.pipeline.arn
          JobDefinition = aws_batch_job_definition.stage.arn
          ContainerOverrides = {
            Environment = [
              { Name = "STAGE", Value = "tts" },
              { Name = "GENERATION_BATCH_DAYS", "Value.$" = "States.Format('{}', $.days)" }
            ]
          }
        }
        Next = "Subtitles"
      }
      Subtitles = {
        Type       = "Task"
        Resource   = "arn:aws:states:::batch:submitJob.sync"
        ResultPath = null
        Parameters = {
          JobName       = "${var.project_name}-subtitles"
          JobQueue      = aws_batch_job_queue.pipeline.arn
          JobDefinition = aws_batch_job_definition.stage.arn
          ContainerOverrides = {
            Environment = [
              { Name = "STAGE", Value = "subtitles" },
              { Name = "GENERATION_BATCH_DAYS", "Value.$" = "States.Format('{}', $.days)" }
            ]
          }
        }
        Next = "Render"
      }
      Render = {
        Type       = "Task"
        Resource   = "arn:aws:states:::batch:submitJob.sync"
        ResultPath = null
        Parameters = {
          JobName       = "${var.project_name}-render"
          JobQueue      = aws_batch_job_queue.pipeline.arn
          JobDefinition = aws_batch_job_definition.render.arn
          ArrayProperties = {
            Size = var.render_array_size
          }
          ContainerOverrides = {
            Environment = [
              { Name = "STAGE", Value = "render" },
              { Name = "GENERATION_BATCH_DAYS", "Value.$" = "States.Format('{}', $.days)" },
              { Name = "RENDER_SHARD_MODE", Value = "array" }
            ]
          }
        }
        Next = "Finalize"
      }
      Finalize = {
        Type       = "Task"
        Resource   = "arn:aws:states:::batch:submitJob.sync"
        ResultPath = null
        Parameters = {
          JobName       = "${var.project_name}-finalize"
          JobQueue      = aws_batch_job_queue.pipeline.arn
          JobDefinition = aws_batch_job_definition.stage.arn
          ContainerOverrides = {
            Environment = [
              { Name = "STAGE", Value = "finalize" },
              { Name = "GENERATION_BATCH_DAYS", "Value.$" = "States.Format('{}', $.days)" }
            ]
          }
        }
        Next = "GenerateDone"
      }
      GenerateDone = {
        Type = "Succeed"
      }
      PublishReady = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.publisher.arn
          "Payload.$"  = "$"
        }
        OutputPath = "$.Payload"
        End        = true
      }
    }
  })

  tags = local.tags
}

resource "aws_iam_role" "scheduler" {
  name = "${var.project_name}-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = {
        Service = "scheduler.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.project_name}-scheduler-policy"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "states:StartExecution"
        Resource = aws_sfn_state_machine.pipeline.arn
      }
    ]
  })
}

resource "aws_scheduler_schedule" "generate" {
  name                         = "${var.project_name}-generate-refill"
  description                  = "Twice-monthly generation refill workflow for publish-ready inventory"
  schedule_expression          = var.generate_schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  state                        = var.enable_schedules ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      mode = "generate"
      days = var.generation_batch_days
    })
  }
}

resource "aws_scheduler_schedule" "upload" {
  name                         = "${var.project_name}-upload-daily"
  description                  = "Daily upload workflow for publish-ready videos"
  schedule_expression          = var.upload_schedule_expression
  schedule_expression_timezone = var.schedule_timezone
  state                        = var.enable_schedules ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn = aws_sfn_state_machine.pipeline.arn
    input = jsonencode({
      mode = "upload"
    })
    role_arn = aws_iam_role.scheduler.arn
  }
}

resource "aws_cloudwatch_event_rule" "step_functions_failed" {
  name        = "${var.project_name}-sfn-failed"
  description = "Alert when the generation/upload state machine fails"
  event_pattern = jsonencode({
    source        = ["aws.states"]
    "detail-type" = ["Step Functions Execution Status Change"]
    detail = {
      status          = ["FAILED", "TIMED_OUT", "ABORTED"]
      stateMachineArn = [aws_sfn_state_machine.pipeline.arn]
    }
  })
  tags = local.tags
}

resource "aws_cloudwatch_event_target" "step_functions_failed" {
  rule      = aws_cloudwatch_event_rule.step_functions_failed.name
  target_id = "sns-alerts"
  arn       = aws_sns_topic.alerts.arn
}

resource "aws_cloudwatch_event_rule" "batch_failed" {
  name        = "${var.project_name}-batch-failed"
  description = "Alert when a Batch job in the pipeline queue fails"
  event_pattern = jsonencode({
    source        = ["aws.batch"]
    "detail-type" = ["Batch Job State Change"]
    detail = {
      status   = ["FAILED"]
      jobQueue = [aws_batch_job_queue.pipeline.arn]
    }
  })
  tags = local.tags
}

resource "aws_cloudwatch_event_target" "batch_failed" {
  rule      = aws_cloudwatch_event_rule.batch_failed.name
  target_id = "sns-alerts"
  arn       = aws_sns_topic.alerts.arn
}
