import os
import time

import boto3


ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")


def _pick_subnet(mode: str) -> str:
    subnets = [item for item in os.environ["SUBNET_IDS"].split(",") if item]
    if not subnets:
        raise RuntimeError("SUBNET_IDS is empty")
    return subnets[0 if mode == "generate" else min(1, len(subnets) - 1)]


def handler(event, context):
    mode = (event or {}).get("mode", "generate").lower().strip()
    if mode not in {"generate", "upload", "both"}:
        raise ValueError(f"unsupported mode: {mode}")

    instance_type = (
        os.environ["GENERATOR_INSTANCE_TYPE"]
        if mode in {"generate", "both"}
        else os.environ["UPLOADER_INSTANCE_TYPE"]
    )
    user_data = ssm.get_parameter(Name=os.environ["USER_DATA_PARAMETER"])["Parameter"]["Value"]
    user_data = user_data.replace("__MODE__", mode)
    name = f"youtube-shorts-{mode}-{int(time.time())}"

    response = ec2.run_instances(
        ImageId=os.environ["AMI_ID"],
        InstanceType=instance_type,
        MinCount=1,
        MaxCount=1,
        IamInstanceProfile={"Name": os.environ["INSTANCE_PROFILE_NAME"]},
        InstanceInitiatedShutdownBehavior="terminate",
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": int(os.environ.get("ROOT_VOLUME_SIZE_GB", "60")),
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                    "Encrypted": True,
                },
            }
        ],
        NetworkInterfaces=[
            {
                "DeviceIndex": 0,
                "SubnetId": _pick_subnet(mode),
                "Groups": [os.environ["SECURITY_GROUP_ID"]],
                "AssociatePublicIpAddress": True,
            }
        ],
        UserData=user_data,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "Project", "Value": "youtube-shorts-automation"},
                    {"Key": "Mode", "Value": mode},
                    {"Key": "ManagedBy", "Value": "terraform"},
                ],
            }
        ],
    )
    instance_ids = [instance["InstanceId"] for instance in response["Instances"]]
    return {"mode": mode, "instance_ids": instance_ids}
