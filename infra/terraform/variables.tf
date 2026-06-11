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

variable "generator_instance_type" {
  type    = string
  default = "m7i.large"
}

variable "uploader_instance_type" {
  type    = string
  default = "t3.micro"
}

variable "root_volume_size_gb" {
  type    = number
  default = 60
}

variable "generate_schedule_expression" {
  type    = string
  default = "cron(0 16 */14 * ? *)"
}

variable "upload_schedule_expression" {
  type    = string
  default = "cron(0 23 * * ? *)"
}

variable "enable_schedules" {
  type    = bool
  default = true
}
