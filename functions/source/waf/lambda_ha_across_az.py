import os
from barbarika.helpers import logger, waitfor
from barbarika.aws import send_response, get_subnet_address, wait_for_reachability_status
from barbarika.citrixadc import CitrixADC


current_aws_region = os.environ['AWS_DEFAULT_REGION']


def lambda_handler(event, context):
    MAX_RETRIES = 60
    fail_reason = None
    logger.info("event: {}".format(str(event)))
    request_type = event['RequestType']
    response_status = 'FAILED'
    response_data = {}
    try:
        if request_type == 'Create':
            primary_instance_id = event['ResourceProperties']['PrimaryADCInstanceID']
            primary_nsip = event['ResourceProperties']['PrimaryADCPrivateNSIP']
            primary_vip = event['ResourceProperties']['PrimaryADCPrivateVIP']
            primary_vip_subnet = event['ResourceProperties']['PrimaryADCVIPPublicSubnetID']

            secondary_instance_id = event['ResourceProperties']['SecondaryADCInstanceID']
            secondary_nsip = event['ResourceProperties']['SecondaryADCPrivateNSIP']
            secondary_vip = event['ResourceProperties']['SecondaryADCPrivateVIP']

            primary = CitrixADC(nsip=primary_nsip,
                                nsuser="nsroot", nspass=primary_instance_id)
            secondary = CitrixADC(
                nsip=secondary_nsip, nsuser="nsroot", nspass=secondary_instance_id)

            # Check if the reachability status is "passed" for both the instances
            # so that bootstrapping is completed
            wait_for_reachability_status(status='passed', max_retries=MAX_RETRIES, adc_ip=primary_nsip ,adc_instanceid=primary_instance_id)
            wait_for_reachability_status(status='passed', max_retries=MAX_RETRIES, adc_ip=secondary_nsip ,adc_instanceid=secondary_instance_id)

            primary.add_hanode(
                id=1, ipaddress=secondary_nsip, incmode=True)
            waitfor(3)
            secondary.add_hanode(
                id=1, ipaddress=primary_nsip, incmode=True)

            # Wait for the password to sync into Secondary-VPX.
            waitfor(
                30, reason='secondary VPX password to get synced to that of primary')

            # From now, the Secondary-VPX password will be that of Primary-VPX
            secondary = CitrixADC(
                nsip=secondary_nsip, nsuser="nsroot", nspass=primary_instance_id)

            ipset_name = 'qs_ipset'
            lbvserver_name = 'qs_lbvserver'

            primary.add_ipset(name=ipset_name)
            secondary.add_ipset(name=ipset_name)

            subnetmask = get_subnet_address(primary_vip_subnet)[1]
            primary.add_nsip(ip=secondary_vip,
                                netmask=subnetmask, iptype='VIP')

            primary.bind_ipset(name=ipset_name, ipaddress=secondary_vip)
            secondary.bind_ipset(name=ipset_name, ipaddress=secondary_vip)

            primary.add_lbvserver(name=lbvserver_name, servicetype='HTTP',
                                    ipaddress=primary_vip, port=80, ipset=ipset_name)

            if secondary.get_lbvserver(name=lbvserver_name):
                logger.info('SUCCESS: {} and {} configured in HA mode'.format(
                    primary.nsip, secondary.nsip))
            else:
                logger.error('FAIL: Could not configure {} and {} in HA mode'.format(
                    primary.nsip, secondary.nsip))

            primary.configure_features(
                ['LB', 'CS', 'SSL', 'WL', 'CR', 'GSLB'])

            primary.save_config()
            secondary.save_config()
            response_status = 'SUCCESS'
        else: # request_type == 'Delete' | 'Update'
            response_status = 'SUCCESS'
    except Exception as e:
        fail_reason = str(e)
        logger.error(fail_reason)
        response_status = 'FAILED'
    finally:
        send_response(event, context, response_status,
                        response_data, fail_reason=fail_reason)
