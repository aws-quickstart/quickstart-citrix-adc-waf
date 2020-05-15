import json
import requests
import boto3
import re
from operator import itemgetter

from .helpers import logger, to_json, cidr_to_netmask, get_gateway_ip

citrix_aws_products = {
    'Citrix ADC VPX - Customer Licensed': '63425ded-82f0-4b54-8cdd-6ec8b94bd4f8',
    'Citrix ADC VPX Express - 20 Mbps': 'daf08ece-57d1-4c0a-826a-b8d9449e3930',

    'Citrix ADC VPX Standard Edition - 10 Mbps': '85bb75fd-34a4-4395-bb19-04b71b20cf3e',
    'Citrix ADC VPX Standard Edition - 200 Mbps': 'dd84ff86-4cea-4c4b-8811-726d079324c7',
    'Citrix ADC VPX Standard Edition - 1000 Mbps': '8328715f-8ad4-4121-af6f-77466a6fd325',
    'Citrix ADC VPX Standard Edition - 3Gbps': 'ecde3c83-e3df-4310-931c-be7164f3c504',
    'Citrix ADC VPX Standard Edition - 5Gbps': '5b010f6b-96e4-4f67-bd66-07022dd5dfec',

    'Citrix ADC VPX Premium Edition - 10 Mbps': '0f7c03e9-ccf7-4b68-815f-0696e1e5770f',
    'Citrix ADC VPX Premium Edition - 200 Mbps': 'a277a667-7f08-44c9-9787-59424b2c50fa',
    'Citrix ADC VPX Premium Edition - 1000 Mbps': '198e217b-a775-4322-8bfe-ab1ea7d598f4',
    'Citrix ADC VPX Premium Edition - 3Gbps': '302979c1-fe98-4344-8a11-c26c88f55e01',
    'Citrix ADC VPX Premium Edition - 5Gbps': '755645a9-d61f-4350-bb91-a6ef204debb3',

    'Citrix ADC VPX Advanced Edition - 10 Mbps': '9ff329b9-3273-4ab0-a7db-d1bd714d4bb3',
    'Citrix ADC VPX Advanced Edition - 200 Mbps': 'fff7ca8f-96a9-4ea7-afa1-279b0d23fe3c',
    'Citrix ADC VPX Advanced Edition - 1000 Mbps': '4e123cf4-fe4c-4afd-a11e-b4280a522de5',
    'Citrix ADC VPX Advanced Edition - 3Gbps': 'd0ebd087-5a71-47e4-8eb0-8bbac8593b43',
    'Citrix ADC VPX Advanced Edition - 5Gbps': 'f67de268-1a70-477e-b135-bf789a9e1d76',
}

ec2_client = boto3.client('ec2')


def send_response(event, context, response_status, response_data, physical_resource_id=None, fail_reason=None):
    response_url = event['ResponseURL']

    logger.info('Lambda Backed Custom resource response: going to respond to ' + response_url)

    response_body = {}
    response_body['Status'] = response_status
    response_body['Reason'] = 'See the details in CloudWatch Log Stream: ' + \
        context.log_stream_name
    if fail_reason is not None:
        response_body['Reason'] += ' :--> FAILED REASON: ' + fail_reason
    response_body['PhysicalResourceId'] = physical_resource_id or context.log_stream_name
    response_body['StackId'] = event['StackId']
    response_body['RequestId'] = event['RequestId']
    response_body['LogicalResourceId'] = event['LogicalResourceId']
    response_body['Data'] = response_data

    json_response_body = json.dumps(response_body)

    logger.info('Lambda Backed Custom resource Response body:\n' + json_response_body)

    headers = {
        'content-type': '',
        'content-length': str(len(json_response_body))
    }

    try:
        response = requests.put(response_url,
                                data=json_response_body,
                                headers=headers)
        logger.info('Lambda Backed Custom resource response success: Status code: ' + response.reason)
    except Exception as e:
        logger.error('Lambda Backed Custom resource response: Failed to post response to ' + response_url + ': ' + str(e))


def get_subnet_address(subnet_id):
    filters = []
    subnets = ec2_client.describe_subnets(
        SubnetIds=[subnet_id], Filters=filters)
    logger.info('subnets: {}'.format(subnets))
    try:
        cidr = subnets['Subnets'][0]['CidrBlock']
        return cidr_to_netmask(cidr)
    except Exception as e:
        logger.error('Could not get subnet details: ' + str(e))

def get_subnet_gateway(subnet_id):
    filters = []
    subnets = ec2_client.describe_subnets(
        SubnetIds=[subnet_id], Filters=filters)
    logger.info('subnets: {}'.format(subnets))
    try:
        cidr = subnets['Subnets'][0]['CidrBlock']
        return get_gateway_ip(cidr)
    except Exception as e:
        logger.error('Could not get subnet details: ' + str(e))

def get_reachability_status(nsip, instID):
    response = ec2_client.describe_instance_status(
        Filters=[],
        InstanceIds=[instID],
    )

    r_status = response['InstanceStatuses'][0]['InstanceStatus']['Details'][0]['Status']
    logger.debug('Rechability Status for {}: {}'.format(nsip, r_status))
    return r_status.strip()


def get_latest_citrixadc_ami(version, product):
    response = ec2_client.describe_images(Filters=[{'Name': 'description', 'Values': ['Citrix NetScaler and CloudBridge Connector {}*'.format(version)]}])
    logger.debug('describe_images response: {}'.format(to_json(response)))
    product_images = []
    for image in response['Images']:
        pattern = r"^Citrix NetScaler and CloudBridge Connector (\d+.\d+-\d+.\d+)-?(64|32)?(-sriov)?-(\w{8}-\w{4}-\w{4}-\w{4}-\w{12})-.*$"
        # Citrix NetScaler and CloudBridge Connector 12.0-60.9-32-sriov-755645a9-d61f-4350-bb91-a6ef204debb3-ami-05fcef5bbc508ad65.4
        name = image['Name']
        z = re.match(pattern, name)
        if z:
            grp = z.groups()
            ProductID = grp[3]
            try:
                if ProductID.strip() == citrix_aws_products[product]:
                    product_images.append(image)
            except KeyError:
                raise Exception('Unknown Product {}', format(product))
        else:
            raise Exception('Do not have any AMIs in the product specified')
    logger.debug('Sorted product images: {}'.format(to_json(product_images)))
    return sorted(product_images, key=itemgetter('CreationDate'), reverse=True)[0]['ImageId']
