import os
from common.helpers import logger
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

            primary = CitrixADC(nsip=primary_nsip,nsuser="nsroot", nspass=primary_instance_id)

            # It is assumed that the reachability status `passed` while calling this lambda function
            try:  # If any of the following step fails, raise exception and return FAILED
                # WAF related
                APPFW_PROFILE = 'QS-Profile'
                APPFW_POLICY = 'QS-Policy'

                # Check if the ADC is licensed for WAF feature, if not exit with error
                isAppfwLicensed = primary.check_license(nsfeature='appfw')
                if not isAppfwLicensed:
                    raise Exception('ADC is not licensed with AppFW feature!')
                else:
                    logger.info('ADCs are licensed for AppFW')

                # enable ns feature AppFw
                primary.configure_features(['AppFw'])

                profileconfig = {
                    "bufferoverflowaction": ['block', 'log', 'stats'],
                    "sqlinjectionaction": ['block', 'log', 'stats'],
                    "crosssitescriptingaction": ['block', 'log', 'stats'],
                    "fileuploadtypesaction": ['log'],
                    "starturlaction": "none",
                    "denyurlaction": "none",
                    "fieldformataction": "none",
                    "crosssitescriptingcheckcompleteurls": "ON",
                    "dosecurecreditcardlogging": "OFF",
                    "responsecontenttype": "application/octet-stream",
                    "excludefileuploadfromchecks": "OFF",
                    "checkrequestheaders": "ON",
                }
                primary.add_appfw_profile(profilename=APPFW_PROFILE, configdict=profileconfig)
                primary.add_appfw_policy(policyname=APPFW_POLICY, profilename=APPFW_PROFILE, rule="true")
                primary.bind_appfw_global_policy(policyname=APPFW_POLICY, priority=100, type="REQ_DEFAULT")

                primary.save_config()
                primary.save_config()

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
