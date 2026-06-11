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

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  tags = {
    Project   = "youtube-shorts-automation"
    ManagedBy = "terraform"
  }

  ssm_parameter_prefix = "/ytshorts"
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    repository_url = var.repository_url
    git_ref        = var.git_ref
    aws_region     = var.aws_region
    bucket_name    = var.bucket_name
  })
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

resource "aws_iam_role" "job" {
  name = "${var.project_name}-ec2-job-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_instance_profile" "job" {
  name = "${var.project_name}-ec2-job-profile"
  role = aws_iam_role.job.name
  tags = local.tags
}

resource "aws_iam_role_policy" "job" {
  name = "${var.project_name}-job-policy"
  role = aws_iam_role.job.id
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
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_parameter_prefix}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "polly:SynthesizeSpeech",
          "polly:DescribeVoices"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role" "launcher" {
  name = "${var.project_name}-launcher-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "launcher_basic" {
  role       = aws_iam_role.launcher.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "launcher" {
  name = "${var.project_name}-launcher-policy"
  role = aws_iam_role.launcher.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:RunInstances",
          "ec2:CreateTags",
          "ec2:DescribeInstances",
          "ec2:DescribeSubnets",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeImages"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.job.arn
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = aws_ssm_parameter.user_data.arn
      }
    ]
  })
}

resource "aws_ssm_parameter" "user_data" {
  name  = "${local.ssm_parameter_prefix}/USER_DATA"
  type  = "String"
  value = local.user_data
  tags  = local.tags
}

data "archive_file" "launcher" {
  type        = "zip"
  source_file = "${path.module}/lambda/launcher.py"
  output_path = "${path.module}/.build/launcher.zip"
}

resource "aws_lambda_function" "launcher" {
  function_name    = "${var.project_name}-launcher"
  role             = aws_iam_role.launcher.arn
  handler          = "launcher.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.launcher.output_path
  source_code_hash = data.archive_file.launcher.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      AMI_ID                  = data.aws_ami.ubuntu.id
      SUBNET_IDS              = join(",", data.aws_subnets.default_public.ids)
      SECURITY_GROUP_ID       = aws_security_group.job.id
      INSTANCE_PROFILE_NAME   = aws_iam_instance_profile.job.name
      GENERATOR_INSTANCE_TYPE = var.generator_instance_type
      UPLOADER_INSTANCE_TYPE  = var.uploader_instance_type
      ROOT_VOLUME_SIZE_GB     = tostring(var.root_volume_size_gb)
      USER_DATA_PARAMETER     = aws_ssm_parameter.user_data.name
    }
  }

  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "generate" {
  name                = "${var.project_name}-generate"
  description         = "Generate new YouTube Shorts on a fixed schedule"
  schedule_expression = var.generate_schedule_expression
  state               = var.enable_schedules ? "ENABLED" : "DISABLED"
  tags                = local.tags
}

resource "aws_cloudwatch_event_rule" "upload" {
  name                = "${var.project_name}-upload"
  description         = "Upload one pending YouTube Short on a fixed schedule"
  schedule_expression = var.upload_schedule_expression
  state               = var.enable_schedules ? "ENABLED" : "DISABLED"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "generate" {
  rule      = aws_cloudwatch_event_rule.generate.name
  target_id = "launch-generate"
  arn       = aws_lambda_function.launcher.arn
  input     = jsonencode({ mode = "generate" })
}

resource "aws_cloudwatch_event_target" "upload" {
  rule      = aws_cloudwatch_event_rule.upload.name
  target_id = "launch-upload"
  arn       = aws_lambda_function.launcher.arn
  input     = jsonencode({ mode = "upload" })
}

resource "aws_lambda_permission" "allow_generate_eventbridge" {
  statement_id  = "AllowGenerateEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.launcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.generate.arn
}

resource "aws_lambda_permission" "allow_upload_eventbridge" {
  statement_id  = "AllowUploadEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.launcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.upload.arn
}
