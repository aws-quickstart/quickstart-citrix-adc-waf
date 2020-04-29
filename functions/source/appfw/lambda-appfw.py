import os
from common.helpers import logger, waitfor
from common.aws import send_response, get_reachability_status, get_subnet_address, get_subnet_gateway
from common.citrixadc import CitrixADC


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
            primary_server_subnet = event['ResourceProperties']['PrimaryADCServerPrivateSubnetID']

            secondary_instance_id = event['ResourceProperties']['SecondaryADCInstanceID']
            secondary_nsip = event['ResourceProperties']['SecondaryADCPrivateNSIP']
            secondary_vip = event['ResourceProperties']['SecondaryADCPrivateVIP']
            secondary_server_subnet = event['ResourceProperties']['SecondaryADCServerPrivateSubnetID']

            is_pooled_license_reqd = True if event['ResourceProperties']['PooledLicense'] == "Yes" else False
            adm_ip = event['ResourceProperties']['ADMIP']
            licensing_mode = event['ResourceProperties']['LicensingMode']
            bandwidth = event['ResourceProperties']['Bandwidth']
            pooled_edition = event['ResourceProperties']['PooledEdition']
            platform = event['ResourceProperties']['Platform']
            cpu_edition = event['ResourceProperties']['VCPUEdition']

            primary = CitrixADC(nsip=primary_nsip,nsuser="nsroot", nspass=primary_instance_id)
            secondary = CitrixADC(nsip=secondary_nsip, nsuser="nsroot", nspass=secondary_instance_id)

            # Check if the reachability status is "passed" for both the instances
            # so that bootstrapping is completed
            retries = 1
            while retries <= MAX_RETRIES:
                if get_reachability_status(primary_nsip, primary_instance_id) == "passed" and \
                        get_reachability_status(secondary_nsip, secondary_instance_id) == "passed":
                    logger.info('Primary and Secondary VPX instances reachability status passed')
                    break
                waitfor(5, "Rechabiliy status is not passed yet. Try No.{}".format(retries))
                retries += 1

            try:  # If any of the following step fails, raise exception and return FAILED
                primary.add_hanode(id=1, ipaddress=secondary_nsip, incmode=True)
                waitfor(3)
                secondary.add_hanode(id=1, ipaddress=primary_nsip, incmode=True)

                # Wait for the password to sync into Secondary-VPX.
                waitfor(30, reason='secondary VPX password to get synced to that of primary')

                # From now, the Secondary-VPX password will be that of Primary-VPX
                secondary = CitrixADC(nsip=secondary_nsip, nsuser="nsroot", nspass=primary_instance_id)

                ipset_name = 'qs_ipset'
                lbvserver_name = 'qs_lbvserver'

                primary.add_ipset(name=ipset_name)
                secondary.add_ipset(name=ipset_name)

                subnetmask = get_subnet_address(primary_vip_subnet)[1]
                primary.add_nsip(ip=secondary_vip,
                                 netmask=subnetmask, iptype='VIP')

                primary.bind_ipset(name=ipset_name, ipaddress=secondary_vip)
                secondary.bind_ipset(name=ipset_name, ipaddress=secondary_vip)

                primary.add_lbvserver(name=lbvserver_name, servicetype='HTTP', ipaddress=primary_vip, port=80, ipset=ipset_name)

                if secondary.get_lbvserver(name=lbvserver_name):
                    logger.info('SUCCESS: {} and {} configured in HA mode'.format(primary.nsip, secondary.nsip))
                else:
                    logger.error('FAIL: Could not configure {} and {} in HA mode'.format(primary.nsip, secondary.nsip))

                primary.configure_features(['LB', 'CS', 'SSL', 'WL', 'CR', 'GSLB'])
                secondary.configure_features(['LB', 'CS', 'SSL', 'WL', 'CR', 'GSLB'])

                primary.save_config()
                secondary.save_config()

                # Optional Pooled licensing support
                if is_pooled_license_reqd:
                    waitfor(100, reason="starting pooled licensing")
                    primary.add_licenseserver(adm_ip)
                    primary.allocate_license(licensing_mode, bandwidth, pooled_edition, platform, cpu_edition)
                    primary.save_config()
                    secondary.save_config()
                    primary.reboot(warm=True)
                    waitfor(100, reason="Warm rebooting {} ".format(primary.nsip))
                    # The above reboot triggers ha-failover. So the secondaryADC becomes new-primaryADC
                    # Reboot secondaryADC (i.e., new-primaryADC) to have the primaryADC as the new-primaryADC
                    secondary.reboot(warm=True)
                    waitfor(100, reason="Warm rebooting {} ".format(secondary.nsip))

                # Primary ADC to send traffic to all the servers (even in other availability zone)
                primary_server_subnet_address = get_subnet_address(primary_server_subnet)
                secondary_server_subnet_address = get_subnet_address(secondary_server_subnet)
                primary_server_gateway = get_subnet_gateway(primary_server_subnet)
                secondary_server_gateway = get_subnet_gateway(secondary_server_subnet)
                primary.add_route(network_ip=secondary_server_subnet_address[0], netmask=secondary_server_subnet_address[1], gateway_ip=primary_server_gateway)
                secondary.add_route(network_ip=primary_server_subnet_address[0], netmask=primary_server_subnet_address[1], gateway_ip=secondary_server_gateway)

                primary.save_config()
                secondary.save_config()

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
