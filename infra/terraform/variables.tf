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

variable "generation_buffer_days" {
  type    = number
  default = 3
}

variable "generation_max_new_items" {
  type    = number
  default = 21
}

variable "reddit_max_posts" {
  type    = number
  default = 60
}

variable "reddit_min_needed" {
  type    = number
  default = 30
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

variable "batch_light_vcpu" {
  type    = string
  default = "1"
}

variable "batch_light_memory_mib" {
  type    = string
  default = "2048"
}

variable "batch_script_vcpu" {
  type    = string
  default = "1"
}

variable "batch_script_memory_mib" {
  type    = string
  default = "2048"
}

variable "batch_render_vcpu" {
  type    = string
  default = "2"
}

variable "batch_render_memory_mib" {
  type    = string
  default = "8192"
}

variable "batch_timeout_seconds" {
  type    = number
  default = 14400
}

variable "render_array_size" {
  type    = number
  default = 21
}

variable "codebuild_compute_type" {
  type    = string
  default = "BUILD_GENERAL1_MEDIUM"
}

variable "youtube_privacy_status" {
  type    = string
  default = "public"
}

variable "youtube_min_upload_bytes" {
  type    = number
  default = 1048576
}

variable "generate_schedule_expression" {
  type    = string
  default = "cron(0 2 1,15 * ? *)"
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

variable "publish_rebase_stale_days" {
  type    = number
  default = 3
}

variable "openai_filter_model" {
  type    = string
  default = "gpt-5.4-nano"
}

variable "openai_script_model" {
  type    = string
  default = "gpt-5.5"
}

variable "monthly_budget_limit_usd" {
  type    = number
  default = 20
}

variable "enable_schedules" {
  type    = bool
  default = true
}
