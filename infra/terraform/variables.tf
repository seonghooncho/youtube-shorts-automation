variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
}

variable "project_name" {
  type    = string
  default = "ytshorts"
}

variable "repository_url" {
  type    = string
  default = "https://github.com/seonghooncho/youtube-shorts-automation.git"
}

variable "git_ref" {
  type    = string
  default = "main"
}

variable "bucket_name" {
  type    = string
  default = "youtube-shorts-automation-160885253413-apne2"
}

variable "source_bundle_key" {
  type    = string
  default = "source/source.zip"
}

variable "generation_batch_days" {
  type    = number
  default = 14
}

variable "reddit_max_posts" {
  type    = number
  default = 30
}

variable "reddit_min_needed" {
  type    = number
  default = 15
}

variable "batch_max_vcpus" {
  type    = number
  default = 32
}

variable "batch_vcpu" {
  type    = string
  default = "2"
}

variable "batch_memory_mib" {
  type    = string
  default = "8192"
}

variable "batch_timeout_seconds" {
  type    = number
  default = 14400
}

variable "codebuild_compute_type" {
  type    = string
  default = "BUILD_GENERAL1_MEDIUM"
}

variable "youtube_privacy_status" {
  type    = string
  default = "private"
}

variable "generate_schedule_expression" {
  type    = string
  default = "cron(0 2 ? * MON *)"
}

variable "upload_schedule_expression" {
  type    = string
  default = "cron(0 8 * * ? *)"
}

variable "schedule_timezone" {
  type    = string
  default = "Asia/Seoul"
}

variable "publish_hour_local" {
  type    = number
  default = 8
}

variable "publish_minute_local" {
  type    = number
  default = 0
}

variable "enable_schedules" {
  type    = bool
  default = true
}
