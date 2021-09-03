import json
import requests
import boto3
import re
from operator import itemgetter

from .helpers import logger, to_json, cidr_to_netmask, get_gateway_ip, waitfor
from . import CITRIX_AWS_PRODUCTS


ec2_client = boto3.client('ec2')


def send_response(event, context, response_status, response_data, physical_resource_id=None, fail_reason=None):
    response_url = event['ResponseURL']

    logger.info(
        'Lambda Backed Custom resource response: going to respond to ' + response_url)

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

    logger.info('Lambda Backed Custom resource Response body:\n' +
                json_response_body)

    headers = {
        'content-type': '',
        'content-length': str(len(json_response_body))
    }

    try:
        response = requests.put(response_url,
                                data=json_response_body,
                                headers=headers)
        logger.info(
            'Lambda Backed Custom resource response success: Status code: ' + response.reason)
    except Exception as e:
        logger.error('Lambda Backed Custom resource response: Failed to post response to ' +
                     response_url + ': ' + str(e))


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
    response = ec2_client.describe_images(Filters=[{'Name': 'description', 'Values': [
                                          'Citrix NetScaler and CloudBridge Connector {}*'.format(version)]}])
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
                if ProductID.strip() == CITRIX_AWS_PRODUCTS[product]:
                    product_images.append(image)
            except KeyError:
                raise Exception('Unknown Product {}', format(product))
        else:
            raise Exception('Do not have any AMIs in the product specified')
    logger.debug('Sorted product images: {}'.format(to_json(product_images)))
    return sorted(product_images, key=itemgetter('CreationDate'), reverse=True)[0]['ImageId']


def wait_for_reachability_status(status, max_retries, adc_ip, adc_instanceid):
    retries = 1
    while retries <= max_retries:
        if get_reachability_status(adc_ip, adc_instanceid) == "passed":
            logger.info(
                'Citrix ADC VPX instances {} reachability status passed'.format(adc_ip))
            break
        waitfor(5, "ADC {} Rechabiliy status is not passed yet. Try No.{}".format(
            adc_ip, retries))
        retries += 1
    else:
        raise Exception('ADC {} did not pass the reachability status after {} tries'.format(
            adc_ip, max_retries))


def assign_secondary_ip_address(eni, ip_list=[], num_of_sec_ip=1):
    response = ec2_client.assign_private_ip_addresses(
        NetworkInterfaceId=eni,
        **(dict(PrivateIpAddresses=ip_list) if ip_list else {}),
        **(dict(SecondaryPrivateIpAddressCount=num_of_sec_ip) if not ip_list else {}),
    )
    return [ip['PrivateIpAddress'] for ip in response['AssignedPrivateIpAddresses']]

def get_enis(instid):
    response = ec2_client.describe_instances(InstanceIds=[instid])
    return response['Reservations'][0]['Instances'][0]['NetworkInterfaces']

def get_vip_eni(instid):
    # VIP has device index as 1 
    enis = get_enis(instid)
    for eni in enis:
        if eni['Attachment']['DeviceIndex'] == 1:
            return eni['NetworkInterfaceId']
    else:
        raise Exception('Could not find VIP ENI for instance-id {}'.format(instid))

def get_snip_eni(instid):
    # VIP has device index as 1 
    enis = get_enis(instid)
    for eni in enis:
        if eni['Attachment']['DeviceIndex'] == 2:
            return eni['NetworkInterfaceId']
    else:
        raise Exception('Could not find SNIP ENI for instance-id {}'.format(instid))


def get_vip_subnet(instid):
    enis = get_enis(instid)
    for eni in enis:
        if eni['Attachment']['DeviceIndex'] == 1:
            return eni['SubnetId']
    else:
        raise Exception('Could not find VIP SubnetID for instance-id {}'.format(instid))


def get_snip_subnet(instid):
    enis = get_enis(instid)
    for eni in enis:
        if eni['Attachment']['DeviceIndex'] == 2:
            return eni['SubnetId']
    else:
        raise Exception('Could not find SNIP SubnetID for instance-id {}'.format(instid))

