terraform {
  backend "s3" {
    bucket         = "ytshorts-terraform-state-160885253413-apne2"
    key            = "youtube-shorts-automation/terraform.tfstate"
    region         = "ap-northeast-2"
    dynamodb_table = "ytshorts-terraform-locks"
    encrypt        = true
  }
}
