import os
from common.helpers import logger, waitfor
from common.aws import send_response
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

            secondary_instance_id = event['ResourceProperties']['SecondaryADCInstanceID']
            secondary_nsip = event['ResourceProperties']['SecondaryADCPrivateNSIP']

            adm_ip = event['ResourceProperties']['ADMIP']
            licensing_mode = event['ResourceProperties']['LicensingMode']
            bandwidth = event['ResourceProperties']['Bandwidth']
            pooled_edition = event['ResourceProperties']['PooledEdition']
            platform = event['ResourceProperties']['Platform']
            cpu_edition = event['ResourceProperties']['VCPUEdition']

            primary = CitrixADC(nsip=primary_nsip,
                                nsuser="nsroot", nspass=primary_instance_id)
            secondary = CitrixADC(
                nsip=secondary_nsip, nsuser="nsroot", nspass=secondary_instance_id)

            try:  # If any of the following step fails, raise exception and return FAILED
                primary.add_licenseserver(adm_ip)
                primary.allocate_license(
                    licensing_mode, bandwidth, pooled_edition, platform, cpu_edition)
                primary.save_config()
                secondary.save_config()
                primary.reboot(warm=True)
                waitfor(100, reason="Warm rebooting {} ".format(primary.nsip))
                # The above reboot triggers ha-failover. So the secondaryADC becomes new-primaryADC
                # Reboot secondaryADC (i.e., new-primaryADC) to have the primaryADC as the new-primaryADC
                secondary.reboot(warm=True)
                waitfor(100, reason="Warm rebooting {} ".format(secondary.nsip))

                primary.save_config()
                secondary.save_config()

                response_status = 'SUCCESS'
            except Exception as e:
                fail_reason = str(e)
                logger.error(fail_reason)
                response_status = 'FAILED'
            finally:
                send_response(event, context, response_status,
                              response_data, fail_reason=fail_reason)
        elif request_type == 'Delete':
            send_response(event, context, 'SUCCESS', {})
        elif request_type == 'Update':
            send_response(event, context, 'SUCCESS', {})
    except Exception as e:
        logger.error('Top level exception {}'.format(str(e)))
        send_response(event, context, 'FAILED', {})
