"""Lambda Python Slack Bot for ECS api`s"""

print "Importing modules"
import json
import logging
import os
import re
import ssl
import decimal
from urlparse import parse_qs
import boto3
import boto
from boto.s3.connection import S3Connection
from boto3.dynamodb.conditions import Key, Attr

print "Loading OS variables"
EXPECTED_TOKEN = os.environ['SlackToken']
DYNAMO_TABLE = os.environ['DynamoConfigTable']
DEPLOYUSERS_TABLE = os.environ['DynamoDeployUsersTable']
IMAGE_REPO = os.environ['ImageRepo']
SHORT_APP = os.environ['ShortApp']
DASHBOARD_RELEASES_BUCKET = os.environ['DashboardReleasesBucket']
RIDER_RELEASES_BUCKET = os.environ['RiderReleasesBucket']
FRONT_BUCKET = os.environ['DashboardBucket']
RIDER_BUCKET = os.environ['RiderBucket']
AWS_REGION = os.environ['AWSRegion']
ENV = os.environ['ENV']
SHORT_ENV = os.environ['ShortENV']
SERVICEROLE_ARN = os.environ['ServiceRoleARN']
APPASGROLE_ARN = os.environ['AppASGRoleARN']
ECSCLUSTER = os.environ['ECSCluster']

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

print "Loading boto3 clients and resources"
CLIENT_ECS = boto3.client('ecs')
CLIENT_DYN = boto3.client('dynamodb')
CLIENT_S3 = boto3.client('s3')
CLIENT_ALB = boto3.client('elbv2')
CLIENT_ASG = boto3.client('application-autoscaling')
CLIENT_C2 = boto3.client('cloudwatch')

RESOURCE_S3 = boto3.resource('s3')
RESOURCE_DYN = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """Starting Handler"""
    params = parse_qs(event['body'])
    token = params['token'][0]
    if token != EXPECTED_TOKEN:
        LOGGER.error("Request token (%s) does not match expected", token)
        return respond(Exception('Invalid request token'))

    user = params['user_name'][0]
    #command = params['command'][0]
    #channel = params['channel_name'][0]
    command_text = params['text'][0]
    arg = command_text.split(' ')
    helpmsg = DEPLOY_HELP + DESCRIBECLUSTERS_HELP + LIST_HELP + TASKS_HELP + CREATESERVICE_HELP
    deployusers = RESOURCE_DYN.Table(DEPLOYUSERS_TABLE)

    if arg[0] == "ping":
        return ping(arg[1], user)

    elif arg[0] == "hi":
        return respondchannel(None, "Hi %s!" % (user))

    elif arg[0] == "describe":
        if arg[1] == "cluster":
            return describeclusters()
        if arg[1] == "config":
            appname = arg[2] + '-' + SHORT_ENV
            return describeappconfig(appname)

    elif arg[0] == "list":
        if arg[1] == "instances":
            return listinstances()
        elif arg[1] == "clusters":
            return listclusters()
        elif arg[1] == "services":
            return listservices()

    elif arg[0] == "task":
        getdeployusers = deployusers.scan(
            Select="ALL_ATTRIBUTES",
            FilterExpression=Attr('username').eq(user)
            )
        if not getdeployusers['Items']:
            return respondchannel(None, 'You have no permission to perform this task, %s' % (user))
        else:
            if arg[1] == "register":
                appname = arg[2] + '-' + SHORT_ENV
                return taskregister(appname, arg[3])
            elif arg[1] == "update":
                appname = arg[2] + '-' + SHORT_ENV
                return taskupdate(appname, arg[3])

    elif arg[0] == "deploy":
        print '*** User starting deploy: ' + user
        getdeployusers = deployusers.scan(
            Select="ALL_ATTRIBUTES",
            FilterExpression=Attr('username').eq(user)
            )
        if not getdeployusers['Items']:
            print '*** You have no permission to deploy, %s' % (user)
            return respondchannel(None, 'You have no permission to deploy, %s' % (user))
        else:
            if arg[1] == "backend":
                appname = arg[2] + '-' + SHORT_ENV
                return deploybackend(user, appname, arg[3])
            if arg[1] == "dashboard":
                print '*** Starting Deploy \n '
                return deployfrontend(user, arg[2], FRONT_BUCKET, DASHBOARD_RELEASES_BUCKET, arg[1])
            if arg[1] == "rider":
                return deployfrontend(user, arg[2], RIDER_BUCKET, RIDER_RELEASES_BUCKET, arg[1])

    elif arg[0] == "create":
        print '*** User starting deploy: ' + user
        getdeployusers = deployusers.scan(
            Select="ALL_ATTRIBUTES",
            FilterExpression=Attr('username').eq(user)
            )
        if not getdeployusers['Items']:
            print '*** You have no permission to deploy, %s' % (user)
            return respondchannel(None, 'You have no permission to deploy, %s' % (user))
        else:
            if arg[1] == "service":
                appname = arg[2] + '-' + SHORT_ENV
                return createservice(user, appname, arg[3])

    elif arg[0] == "help":
        topmsg = "Always use `/luke` or `luke_staging` before every command\n\n See reference:\n\n"
        return respondchannel(None, helpmsg, topmsg)

    else:
        return respondchannel(None, helpmsg, "*UNRECOGNIZED COMMAND*\n\n See reference:")

def respondchannel(err, res=None, title=None):
    """Send Message to SLACK publicly"""
    dump = {
        'response_type': "in_channel",
        'attachments': [{
            'pretext': title,
            'text': res,
            'mrkdwn_in': [
                "text",
                "pretext"
            ]
        }]
    }

    return {
        'statusCode': '400' if err else '200',
        'body': err.message if err else json.dumps(dump, sort_keys=True, separators=(',', ': ')),
        'headers': {
            'Content-Type': 'application/json',
        },
    }

def respond(err, res=None):
    """Send Message to SLACK privatly"""
    return {
        'statusCode': '400' if err else '200',
        'body': err.message if err else json.dumps(res, indent=4),
        'headers': {
            'Content-Type': 'application/json',
        },
    }

def ping(who, user):
    """Respond ping request by USER"""
    if who == "sith":
        return respondchannel(None, None, "You should not go to the dark side %s" % (user))
    elif who == "jedi":
        return respondchannel(None, None, "I am one with the force. May the force be with you!")


DESCRIBECLUSTERS_MSG = "- describe cluster\n - describe config _appname_\n\n "
DESCRIBECLUSTERS_EX = "Example:\n `describe cluster`\n `describe config apigateway-eu`\n\n\n"
DESCRIBECLUSTERS_HELP = "*DESCRIBE:*\n " + DESCRIBECLUSTERS_MSG + DESCRIBECLUSTERS_EX

def describeclusters():
    """Respond CLUSTER information"""
    describeecsclusters = CLIENT_ECS.describe_clusters(
        clusters=[
            ECSCLUSTER
            ]
        )
    for majorkey, subdict in describeecsclusters.iteritems():
        if majorkey == 'clusters':
            msg = ("*Cluster %s Description:*\n\n" % (ECSCLUSTER))
            response = msg + (re.sub(r",\s(?![^(]*\))", "\n", "%s" % (subdict))[2:-2])
            return respondchannel(None, response)

def describeappconfig(appname):
    """Respond APP stored configuration"""
    class DecimalEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, decimal.Decimal):
                if o % 1 > 0:
                    return float(o)
                else:
                    return int(o)
            return super(DecimalEncoder, self).default(o)

    table = RESOURCE_DYN.Table(DYNAMO_TABLE)
    response = table.scan(
        Select="ALL_ATTRIBUTES",
        FilterExpression=Attr('app').eq(appname)
        )

    for i in response['Items']:
        response = json.dumps(i, cls=DecimalEncoder, indent=4)
        return respondchannel(None, response)


LIST_MSG = "- list clusters\n - list instances\n - list services\n\n "
LIST_EX = "Example:\n `list clusters`\n `list instances`\n `list services`\n\n\n"
LIST_HELP = "*LIST*:\n " + LIST_MSG + LIST_EX

def listservices():
    """List all services deployed on cluster"""
    listecsservices = CLIENT_ECS.list_services(
        cluster=ECSCLUSTER
    )
    for majorkey, subdict in listecsservices.iteritems():
        if majorkey == 'serviceArns':
            msg = ("*Cluster %s Services ARNs:*\n\n" % (ECSCLUSTER))
            response = msg + (re.sub(r",\s(?![^(]*\))", "\n", "%s" % (subdict))[1:-1])
            return respondchannel(None, response)

def listclusters():
    """List available clusters"""
    listecsclusters = CLIENT_ECS.list_clusters(
    )
    for majorkey, subdict in listecsclusters.iteritems():
        if majorkey == 'clusterArns':
            msg = "*Clusters ARNs:*\n\n"
            response = msg + (re.sub(r",\s(?![^(]*\))", "\n", "%s" % (subdict))[1:-1])
            return respondchannel(None, response)

def listinstances():
    """List cluster instances"""
    listecsinstances = CLIENT_ECS.list_container_instances(
        cluster=ECSCLUSTER,
    )
    for majorkey, subdict in listecsinstances.iteritems():
        if majorkey == 'containerInstanceArns':
            msg = ("* %s Instances ARNs:*\n\n" % (ECSCLUSTER))
            response = msg + (re.sub(r",\s(?![^(]*\))", "\n", "%s" % (subdict))[1:-1])
            return respondchannel(None, response)


TASK_MSG = "- task register _appname_ _appversion_\n - task update _appname_ _definition_\n\n "
TASK_EX_PT1 = "Example:\n `task register apigateway-eu v1`\n "
TASK_EX_PT2 = "`task update apigateway-eu apigateway-eu-dev:1`\n\n\n"
TASKS_HELP = "*TASKS:*\n " + TASK_MSG + TASK_EX_PT1 + TASK_EX_PT2

def taskupdate(ecsservice, ecsdefinition):
    """Update service with new tas definition"""
    updateservice = CLIENT_ECS.update_service(
        cluster=ECSCLUSTER,
        service=ecsservice,
        taskDefinition=ecsdefinition
    )
    print updateservice
    msg_pt1 = "*Task Updated*\n\n "
    msg_pt2 = "Service: %s\n Cluster: %s\n " % (ecsservice, ECSCLUSTER)
    msg_pt3 = "Updated to definition: %s" % (ecsdefinition)
    response = msg_pt1 + msg_pt2 + msg_pt3
    return respondchannel(None, response)

def taskregister(appname, appversion):
    """Register a new task definition"""
    deployimagerepo = IMAGE_REPO + '/' + SHORT_APP + '-' + appname[:-7]
    table = RESOURCE_DYN.Table(DYNAMO_TABLE)
    getconfig = table.query(
        KeyConditionExpression=Key('app').eq(appname)
    )

    for i in getconfig['Items']:

        local_folder = (i['local_folder'])
        container_cpu = (i['container_cpu'])
        container_mem = (i['container_mem'])
        containermemreserv = (i['containermemreserv'])
        container_port = (i['containerPort'])
        host_port = (i['hostPort'])
        env_vars_list = (i['env_vars_list'])
        containerlogsourcepath = (i['containerlogsourcepath'])
        awslogs_group = (i['awslogs_group'])

        registertaskdefinition = CLIENT_ECS.register_task_definition(
            family=appname,
            volumes=[
                {
                    'name': 'local_folder_logs',
                    'host': {
                        'sourcePath': local_folder
                    }
                }
            ],
            containerDefinitions=[
                {
                    'name' : appname,
                    'image' : "%s:%s" % (deployimagerepo, appversion),
                    'cpu' : int(float(container_cpu)),
                    'memory' : int(float(container_mem)),
                    'memoryReservation' : int(float(containermemreserv)),
                    'portMappings' : [
                        {
                            "containerPort": int(float(container_port)),
                            "hostPort": int(float(host_port)),
                            "protocol": "tcp"
                        }
                    ],
                    'essential' : True,
                    'environment' : env_vars_list,
                    'logConfiguration': {
                        'logDriver': 'awslogs',
                        'options': {
                            "awslogs-group": awslogs_group,
                            "awslogs-region": AWS_REGION,
                            "awslogs-stream-prefix": appname
                        }
                    },
                    'mountPoints': [
                        {
                            'sourceVolume': 'local_folder_logs',
                            'containerPath': containerlogsourcepath,
                            'readOnly': False
                        }
                    ]
                }
            ]
        )
        print registertaskdefinition
        msg_pt1 = "*New Revision*\n\n Task: %s\n Registerd\n\n " % (appname)
        msg_pt2 = "May the force be with you!  :rebel:"
        response = msg_pt1 + msg_pt2
        return respondchannel(None, response)


DEPLOY_MSG_PT1 = "- deploy backend _appname_ _appversion_\n "
DEPLOY_MSG_PT2 = "- deploy dashboard _appversion_\n - deploy rider _appversion_\n\n "
DEPLOY_EX_PT1 = "Example:\n`deploy backend apigateway-eu v1`\n"
DEPLOY_EX_PT2 = "`deploy dashboard ########`\n`deploy rider ########`\n\n\n"
DEPLOY_HELP = "*DEPLOY*\n " + DEPLOY_MSG_PT1 + DEPLOY_MSG_PT2 + DEPLOY_EX_PT1 + DEPLOY_EX_PT2

def deployfrontend(user, appversion, deploymentbucket, releasesbucket, service):
    """Deployment on S3 static hosting bucket"""
    if hasattr(ssl, '_create_unverified_context'):
        ssl._create_default_https_context = ssl._create_unverified_context
        conn = boto.connect_s3()
        getdeploybucket = conn.get_bucket(deploymentbucket)
        bucket_location = getdeploybucket.get_location()
        conn = boto.s3.connect_to_region(bucket_location)
        getdeploybucket = conn.get_bucket(deploymentbucket)

        for key in getdeploybucket.list():
            getdeploybucket.delete_key(key)

    conn = S3Connection()
    getreleasebucket = conn.get_bucket(releasesbucket)

    for key in getreleasebucket.list():
        if key.name.encode('utf-8') != appversion + '/':
            if key.name.encode('utf-8')[:6] == appversion:
                copys3files = CLIENT_S3.copy_object(
                    CopySource=releasesbucket + '/' + key.name.encode('utf-8'),
                    Bucket=deploymentbucket,
                    Key=key.name.encode('utf-8')[7:]
                )

    msg_pt1 = "*S3 Deployment Done*\n\n User: %s\n " % (user)
    msg_pt2 = "Bucket: %s\n Service: %s\n " % (deploymentbucket, service)
    msg_pt3 = "Version Deployed: %s\n\n\n May the force be with you!  :rebel:\n\n" % (appversion)
    response = msg_pt1 + msg_pt2 + msg_pt3
    return respondchannel(None, response)

def deploybackend(user, appname, appversion):
    """Deploy ECS service"""
    print '*** Starting Deploy'
    print '* Appname: ' + appname
    print '* Version: ' + appversion
    print '* User: ' + user

    deployimagerepo = IMAGE_REPO + '/' + SHORT_APP + '-' + appname[:-7]
    ecs_service_name = SHORT_APP + '-' + appname[:-7]
    print '*** ' + deployimagerepo + ' ***'
    table = RESOURCE_DYN.Table(DYNAMO_TABLE)
    getconfig = table.query(
        KeyConditionExpression=Key('app').eq(appname)
    )

    for i in getconfig['Items']:
        print getconfig

        local_folder = (i['local_folder'])
        container_cpu = (i['container_cpu'])
        container_mem = (i['container_mem'])
        containermemreserv = (i['containermemreserv'])
        container_port = (i['containerPort'])
        host_port = (i['hostPort'])
        env_vars_list = (i['env_vars_list'])
        containerlogsourcepath = (i['containerlogsourcepath'])
        awslogs_group = (i['awslogs_group'])

        registertaskdefinition = CLIENT_ECS.register_task_definition(
            family=appname,
            volumes=[
                {
                    'name': 'local_folder_logs',
                    'host': {
                        'sourcePath': local_folder
                    }
                }
            ],
            containerDefinitions=[
                {
                    'name' : appname,
                    'image' : "%s:%s" % (deployimagerepo, appversion),
                    'cpu' : int(float(container_cpu)),
                    'memory' : int(float(container_mem)),
                    'memoryReservation' : int(float(containermemreserv)),
                    'portMappings' : [
                        {
                            "containerPort": int(float(container_port)),
                            "hostPort": int(float(host_port)),
                            "protocol": "tcp"
                        }
                    ],
                    'essential' : True,
                    'environment' : env_vars_list,
                    'logConfiguration': {
                        'logDriver': 'awslogs',
                        'options': {
                            "awslogs-group": awslogs_group,
                            "awslogs-region": AWS_REGION,
                            "awslogs-stream-prefix": appname
                        }
                    },
                    'mountPoints': [
                        {
                            'sourceVolume': 'local_folder_logs',
                            'containerPath': containerlogsourcepath,
                            'readOnly': False
                        }
                    ]
                }
            ]
        )
        for majorkey, subdict in registertaskdefinition.iteritems():
            for subkey, value in subdict.iteritems():
                if subkey == 'revision':
                    updateservice = CLIENT_ECS.update_service(
                        cluster=ECSCLUSTER,
                        service=ecs_service_name,
                        taskDefinition="%s:%s" % (appname, value)
                        )
                    print updateservice

                    msg_pt1 = "*ECS Deployment Done*\n\n User: %s\n " % (user)
                    msg_pt2 = "Cluster: %s\n Service: %s\n " % (ECSCLUSTER, appname)
                    msg_pt3 = "Version Deployed: %s\n " % (appversion)
                    msg_pt4 = "Updated to definition: %s\n\n\n " % (value)
                    msg_pt5 = "May the force be with you!  :rebel:"
                    response = msg_pt1 + msg_pt2 + msg_pt3 + msg_pt4 + msg_pt5
                    return respondchannel(None, response)


CREATESERVICE_MSG = "- create service _appname_ _appversion_\n\n "
CREATESERVICE_EX = "Example:\n`create service apigateway-eu v1`\n\n\n"
CREATESERVICE_HELP = "*CREATE*\n " + CREATESERVICE_MSG + CREATESERVICE_EX

def createservice(user, appname, appversion):
    """Create ECS service"""
    print '* Starting Creation of Service ***'
    print '* Appname: ' + appname
    print '* Version: ' + appversion
    print '* User: ' + user

    deployimagerepo = IMAGE_REPO + '/' + SHORT_APP + '-' + appname[:-7]
    print '*** Image Repo: ' + deployimagerepo + ' ***'
    table = RESOURCE_DYN.Table(DYNAMO_TABLE)
    getconfig = table.query(
        KeyConditionExpression=Key('app').eq(appname)
    )

    for i in getconfig['Items']:
        print getconfig

        subnets = (i['albsubnets'])
        securitygroups = (i['albsecuritygroups'])
        scheme = (i['albscheme'])
        protocol = (i['albhealthcheckprotocol'])
        port = (i['albport'])
        vpcid = (i['vpcid'])
        healthcheckport = (i['albhealthcheckport'])
        healthcheckpath = (i['albapplicationhealthcheckurl'])
        healthcheckintervalseconds = (i['albhealthcheckintervalseconds'])
        healthchecktimeoutseconds = (i['albhealthchecktimeoutseconds'])
        healthythresholdcount = (i['albhealthythresholdcount'])
        unhealthythresholdcount = (i['albunhealthythresholdcount'])
        memoryviewatcherhttpcode = (i['albmatcherhttpcode'])
        deregistrationdelay = (i['albderegistrationdelay'])
        maximumpercent = (i['ecsdeployconfigmaximumPercent'])
        minimumhealthypercent = (i['ecsdeployconfigminimumHealthyPercent'])
        desiredcount = (i['ecsdesiredCount'])
        containerport = (i['containerPort'])
        mincapacity = (i['ecsappasgMinCapacity'])
        maxcapacity = (i['ecsappasgMaxCapacity'])
        period = (i['ecsappasgPeriod'])
        statistic = (i['ecsappasgStatistic'])
        scalingadjustmentup = (i['ecsappasgScalingAdjustmentUp'])
        scalingadjustmentdown = (i['ecsappasgScalingAdjustmentDown'])
        comparisonoperatorscaleup = (i['ecsappasgComparisonOperatorScaleUp'])
        comparisonoperatorscaledown = (i['ecsappasgComparisonOperatorScaleDown'])
        cpuusagescaleup = (i['ecsappasgCpuUsageScaleUp'])
        cpuusagescaledown = (i['ecsappasgCpuUsageScaleDown'])
        evaluationperiods = (i['ecsappasgEvaluationPeriods'])
        local_folder = (i['local_folder'])
        container_cpu = (i['container_cpu'])
        container_mem = (i['container_mem'])
        containermemreserv = (i['containermemreserv'])
        host_port = (i['hostPort'])
        env_vars_list = (i['env_vars_list'])
        containerlogsourcepath = (i['containerlogsourcepath'])
        awslogs_group = (i['awslogs_group'])

        ecs_service_name = SHORT_APP + '-' + appname[:-7]
        policynameup = ecs_service_name+"-cpu-up"
        policynamedown = ecs_service_name+"-cpu-down"
        cpu_up = ecs_service_name+"-cpu-up"
        cpu_down = ecs_service_name+"-cpu-down"
        service = "service/"+ECSCLUSTER+"/"+ecs_service_name
        ecs_task_def_name = appname
        servicerolearn = SERVICEROLE_ARN
        appasgrolearn = APPASGROLE_ARN

        createapplb = CLIENT_ALB.create_load_balancer(
            Name=SHORT_APP + '-' + appname,
            Subnets=subnets,
            SecurityGroups=securitygroups,
            Scheme=scheme,
            Tags=[
                {
                    'Key': 'Env',
                    'Value': ENV
                },
            ]
        )

        createalbtg = CLIENT_ALB.create_target_group(

            Name=SHORT_APP + '-' + appname,
            Protocol=protocol,
            Port=int(float(port)),
            VpcId=vpcid,
            HealthCheckProtocol=protocol,
            HealthCheckPort=healthcheckport,
            HealthCheckPath=healthcheckpath,
            HealthCheckIntervalSeconds=int(float(healthcheckintervalseconds)),
            HealthCheckTimeoutSeconds=int(float(healthchecktimeoutseconds)),
            HealthyThresholdCount=int(float(healthythresholdcount)),
            UnhealthyThresholdCount=int(float(unhealthythresholdcount)),
            Matcher={
                'HttpCode': memoryviewatcherhttpcode
            }
        )

        change_draining = CLIENT_ALB.modify_target_group_attributes(
            TargetGroupArn=createalbtg['TargetGroups'][0]['TargetGroupArn'],
            Attributes=[
                {
                    'Key': 'deregistration_delay.timeout_seconds',
                    'Value': deregistrationdelay
                },
            ]
        )
        print 'Creating ALB Target Group'
        print change_draining

        loadbalancerarn = createapplb['LoadBalancers'][0]['LoadBalancerArn']

        print 'Creating ALB'
        createalblistener = CLIENT_ALB.create_listener(
            LoadBalancerArn=loadbalancerarn,
            Protocol=protocol,
            Port=int(float(port)),
            DefaultActions=[
                {
                    'Type': 'forward',
                    'TargetGroupArn': createalbtg['TargetGroups'][0]['TargetGroupArn']
                }
            ]
        )
        print createalblistener
        print 'Creating ALB Listener'

        registertaskdefinition = CLIENT_ECS.register_task_definition(
            family=appname,
            volumes=[
                {
                    'name': 'local_folder_logs',
                    'host': {
                        'sourcePath': local_folder
                    }
                }
            ],
            containerDefinitions=[
                {
                    'name' : appname,
                    'image' : "%s:%s" % (deployimagerepo, appversion),
                    'cpu' : int(float(container_cpu)),
                    'memory' : int(float(container_mem)),
                    'memoryReservation' : int(float(containermemreserv)),
                    'portMappings' : [
                        {
                            "containerPort": int(float(containerport)),
                            "hostPort": int(float(host_port)),
                            "protocol": "tcp"
                        }
                    ],
                    'essential' : True,
                    'environment' : env_vars_list,
                    'logConfiguration': {
                        'logDriver': 'awslogs',
                        'options': {
                            "awslogs-group": awslogs_group,
                            "awslogs-region": AWS_REGION,
                            "awslogs-stream-prefix": appname
                        }
                    },
                    'mountPoints': [
                        {
                            'sourceVolume': 'local_folder_logs',
                            'containerPath': containerlogsourcepath,
                            'readOnly': False
                        }
                    ]
                }
            ]
        )
        print 'Registering Task Definition'
        print registertaskdefinition

        describetg = CLIENT_ALB.describe_target_groups(
            Names=[
                SHORT_APP + '-' + appname
            ]
        )

        targetgrouparn = describetg['TargetGroups'][0]['TargetGroupArn']
        createecsservice = CLIENT_ECS.create_service(
            cluster=ECSCLUSTER,
            serviceName=ecs_service_name,
            desiredCount=int(float(desiredcount)),
            taskDefinition=ecs_task_def_name,
            role=servicerolearn,
            loadBalancers=[
                {
                    'targetGroupArn': targetgrouparn,
                    'containerName': appname,
                    'containerPort': int(float(containerport))
                },
            ],
            deploymentConfiguration={
                'maximumPercent': int(float(maximumpercent)),
                'minimumHealthyPercent': int(float(minimumhealthypercent))
            }
        )
        print 'Creating ECS Service'
        print createecsservice

        registerasgscalabletarget = CLIENT_ASG.register_scalable_target(
            ServiceNamespace='ecs',
            ResourceId=service,
            ScalableDimension='ecs:service:DesiredCount',
            MinCapacity=int(float(mincapacity)),
            MaxCapacity=int(float(maxcapacity)),
            RoleARN=appasgrolearn
        )
        print 'Registering ASG ScalableTarget'
        print registerasgscalabletarget

        putasgscalingpolicyup = CLIENT_ASG.put_scaling_policy(
            PolicyName=policynameup,
            ServiceNamespace='ecs',
            ResourceId=service,
            ScalableDimension='ecs:service:DesiredCount',
            PolicyType='StepScaling',
            StepScalingPolicyConfiguration={
                'AdjustmentType': 'ChangeInCapacity',
                'StepAdjustments': [
                    {
                        'ScalingAdjustment': int(float(scalingadjustmentup)),
                        "MetricIntervalLowerBound": 0.0
                    },
                ],
                'Cooldown': int(float(period)),
                'MetricAggregationType': statistic
            }
        )

        putasgscalingpolicydown = CLIENT_ASG.put_scaling_policy(
            PolicyName=policynamedown,
            ServiceNamespace='ecs',
            ResourceId=service,
            ScalableDimension='ecs:service:DesiredCount',
            PolicyType='StepScaling',
            StepScalingPolicyConfiguration={
                'AdjustmentType': 'ChangeInCapacity',
                'StepAdjustments': [
                    {
                        'ScalingAdjustment': int(float(scalingadjustmentdown)),
                        "MetricIntervalUpperBound": 0.0
                    },
                ],
                'Cooldown': int(float(period)),
                'MetricAggregationType': statistic
            }
        )

        print 'Setting up CW Policies'
        response_cpu_up = putasgscalingpolicyup['PolicyARN']
        response_cpu_down = putasgscalingpolicydown['PolicyARN']

        alarme_up = CLIENT_C2.put_metric_alarm(
            AlarmName=cpu_up,
            AlarmDescription=cpu_up,
            MetricName='CPUUtilization',
            Namespace='AWS/ECS',
            Statistic=statistic,
            Dimensions=[
                {
                    "Name": "ClusterName",
                    "Value": ECSCLUSTER
                },
                {
                    "Name": "ServiceName",
                    "Value": ecs_service_name
                }
            ],
            Period=int(float(period)),
            Unit='Percent',
            EvaluationPeriods=int(float(evaluationperiods)),
            Threshold=int(float(cpuusagescaleup)),
            ComparisonOperator=comparisonoperatorscaleup,
            AlarmActions=[
                response_cpu_up,
            ],
        )
        print alarme_up
        

        alarme_down = CLIENT_C2.put_metric_alarm(
            AlarmName=cpu_down,
            AlarmDescription=cpu_down,
            MetricName='CPUUtilization',
            Namespace='AWS/ECS',
            Statistic=statistic,
            Dimensions=[
                {
                    "Name": "ClusterName",
                    "Value": ECSCLUSTER
                },
                {
                    "Name": "ServiceName",
                    "Value": ecs_service_name
                }
            ],
            Period=int(float(period)),
            Unit='Percent',
            EvaluationPeriods=int(float(evaluationperiods)),
            Threshold=int(float(cpuusagescaledown)),
            ComparisonOperator=comparisonoperatorscaledown,
            AlarmActions=[
                response_cpu_down,
            ],
        )
        print alarme_down

        msg_pt1 = "*ECS Create Service Done*\n\n User: %s\n Cluster: %s\n\n\n " % (user, ECSCLUSTER)
        msg_pt2 = "*Resources Created:*\n\n Application Load Balancer: %s\n " % (loadbalancerarn)
        msg_pt3 = "Target Group: %s\n " % (targetgrouparn)
        msg_pt4 = "CloudWatch Alarms\n Service: %s\n " % (appname)
        msg_pt5 = "Version Deployed: %s\n\n\n May the force be with you!  :rebel:" % (appversion)
        response = msg_pt1 + msg_pt2 + msg_pt3 + msg_pt4 + msg_pt5
        return respondchannel(None, response)
