variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
}

variable "state_bucket_name" {
  type    = string
  default = "ytshorts-terraform-state-160885253413-apne2"
}

variable "lock_table_name" {
  type    = string
  default = "ytshorts-terraform-locks"
}
