import os
from common.helpers import logger
from common.aws import send_response, get_subnet_address, get_subnet_gateway
from common.citrixadc import CitrixADC


current_aws_region = os.environ['AWS_DEFAULT_REGION']


def lambda_handler(event, context):
    fail_reason = None
    logger.info("event: {}".format(str(event)))
    request_type = event['RequestType']
    response_status = 'FAILED'
    response_data = {}
    try:
        if request_type == 'Create':
            primary_instance_id = event['ResourceProperties']['PrimaryADCInstanceID']
            primary_nsip = event['ResourceProperties']['PrimaryADCPrivateNSIP']
            primary_server_subnet = event['ResourceProperties']['PrimaryADCServerPrivateSubnetID']

            secondary_instance_id = event['ResourceProperties']['SecondaryADCInstanceID']
            secondary_nsip = event['ResourceProperties']['SecondaryADCPrivateNSIP']
            secondary_server_subnet = event['ResourceProperties']['SecondaryADCServerPrivateSubnetID']

            primary = CitrixADC(nsip=primary_nsip,nsuser="nsroot", nspass=primary_instance_id)
            secondary = CitrixADC(nsip=secondary_nsip, nsuser="nsroot", nspass=secondary_instance_id)

            try:  # If any of the following step fails, raise exception and return FAILED
                # Primary ADC to send traffic to all the servers (even in other availability zone)
                primary_server_subnet_address = get_subnet_address(primary_server_subnet)
                secondary_server_subnet_address = get_subnet_address(secondary_server_subnet)
                primary_server_gateway = get_subnet_gateway(primary_server_subnet)
                secondary_server_gateway = get_subnet_gateway(secondary_server_subnet)
                primary.add_route(network_ip=secondary_server_subnet_address[0], netmask=secondary_server_subnet_address[1], gateway_ip=primary_server_gateway)
                secondary.add_route(network_ip=primary_server_subnet_address[0], netmask=primary_server_subnet_address[1], gateway_ip=secondary_server_gateway)

                primary.save_config()
                secondary.save_config()

                response_status = 'SUCCESS'
            except Exception as e:
                fail_reason = str(e)
                logger.error(fail_reason)
                response_status = 'FAILED'
            finally:
                send_response(event, context, response_status,response_data, fail_reason=fail_reason)
        elif request_type == 'Delete':
            send_response(event, context, 'SUCCESS', {})
        elif request_type == 'Update':
            send_response(event, context, 'SUCCESS', {})
    except Exception as e:
        logger.error('Top level exception {}'.format(str(e)))
        send_response(event, context, 'FAILED', {})
