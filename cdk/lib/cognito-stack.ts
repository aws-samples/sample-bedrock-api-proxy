import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';
import { EnvironmentConfig } from '../config/config';

export interface CognitoStackProps extends cdk.StackProps {
  config: EnvironmentConfig;
}

export class CognitoStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly userPoolDomain: cognito.UserPoolDomain;

  constructor(scope: Construct, id: string, props: CognitoStackProps) {
    super(scope, id, props);

    const { config } = props;

    // Create Cognito User Pool
    this.userPool = new cognito.UserPool(this, 'AdminUserPool', {
      userPoolName: `anthropic-proxy-admin-${config.environmentName}`,
      selfSignUpEnabled: false, // Admin creates users
      signInAliases: {
        email: true,
        username: false,
      },
      autoVerify: {
        email: true,
      },
      standardAttributes: {
        email: {
          required: true,
          mutable: true,
        },
        givenName: {
          required: false,
          mutable: true,
        },
        familyName: {
          required: false,
          mutable: true,
        },
      },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
        tempPasswordValidity: cdk.Duration.days(7),
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: config.environmentName === 'prod'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      mfa: config.environmentName === 'prod'
        ? cognito.Mfa.OPTIONAL
        : cognito.Mfa.OFF,
      mfaSecondFactor: {
        sms: false,
        otp: true,
      },
      email: cognito.UserPoolEmail.withCognito(),
    });

    // Create User Pool Domain (for hosted UI if needed)
    this.userPoolDomain = this.userPool.addDomain('Domain', {
      cognitoDomain: {
        domainPrefix: `anthropic-proxy-admin-${config.environmentName}-${cdk.Aws.ACCOUNT_ID}`,
      },
    });

    // Create App Client (for SPA - no client secret)
    // Note: This app uses USER_PASSWORD_AUTH and USER_SRP_AUTH flows (direct SDK auth),
    // NOT OAuth redirects. The callback/logout URLs below are only needed if using
    // the Cognito hosted UI. Since we use direct auth, these are for development only.
    this.userPoolClient = this.userPool.addClient('AdminPortalClient', {
      userPoolClientName: `admin-portal-${config.environmentName}`,
      generateSecret: false, // SPA cannot keep secrets
      authFlows: {
        userPassword: true, // Enable USER_PASSWORD_AUTH
        userSrp: true, // Enable SRP auth (more secure)
      },
      oAuth: {
        flows: {
          authorizationCodeGrant: true,
          implicitCodeGrant: false, // Less secure, avoid
        },
        scopes: [
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.PROFILE,
        ],
        // Note: Cognito does NOT support wildcard callback URLs.
        // These localhost URLs are for development. For production with custom domains,
        // add the specific domain URL via AWS Console or update this after deployment.
        callbackUrls: [
          'http://localhost:5173/admin/', // Local Vite dev server
          'http://localhost:8005/admin/', // Local backend dev server
        ],
        logoutUrls: [
          'http://localhost:5173/admin/',
          'http://localhost:8005/admin/',
        ],
      },
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
      preventUserExistenceErrors: true,
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.COGNITO,
      ],
    });

    // Apply tags
    Object.entries(config.tags).forEach(([key, value]) => {
      cdk.Tags.of(this.userPool).add(key, value);
    });

    // Outputs
    new cdk.CfnOutput(this, 'UserPoolId', {
      value: this.userPool.userPoolId,
      description: 'Cognito User Pool ID',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: this.userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
    });

    new cdk.CfnOutput(this, 'UserPoolDomainName', {
      value: this.userPoolDomain.domainName,
      description: 'Cognito User Pool Domain',
    });

    new cdk.CfnOutput(this, 'CognitoRegion', {
      value: config.region,
      description: 'Cognito Region',
    });
  }
}
