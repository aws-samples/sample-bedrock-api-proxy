#!/usr/bin/env python3
"""
Setup script for AWS Cognito User Pool.

Creates a Cognito User Pool, App Client, and initial admin user for the admin portal.
"""
import argparse
import json
import secrets
import string
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def generate_temporary_password(length: int = 10) -> str:
    """Generate a secure temporary password meeting Cognito requirements."""
    # Ensure at least one of each required character type
    lowercase = secrets.choice(string.ascii_lowercase)
    uppercase = secrets.choice(string.ascii_uppercase)
    digit = secrets.choice(string.digits)

    # Fill the rest with random characters (letters and digits only)
    remaining_length = length - 3
    all_chars = string.ascii_letters + string.digits
    remaining = "".join(secrets.choice(all_chars) for _ in range(remaining_length))

    # Combine and shuffle
    password_list = list(lowercase + uppercase + digit + remaining)
    secrets.SystemRandom().shuffle(password_list)
    return "".join(password_list)


def create_user_pool(cognito_client, pool_name: str) -> dict:
    """Create a Cognito User Pool with appropriate settings."""
    print(f"Creating User Pool: {pool_name}...")

    try:
        response = cognito_client.create_user_pool(
            PoolName=pool_name,
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 10,
                    "RequireUppercase": True,
                    "RequireLowercase": True,
                    "RequireNumbers": True,
                    "RequireSymbols": False,
                    "TemporaryPasswordValidityDays": 7,
                }
            },
            AutoVerifiedAttributes=["email"],
            UsernameAttributes=["email"],
            MfaConfiguration="OFF",
            UserAttributeUpdateSettings={
                "AttributesRequireVerificationBeforeUpdate": ["email"]
            },
            Schema=[
                {
                    "Name": "email",
                    "AttributeDataType": "String",
                    "Required": True,
                    "Mutable": True,
                },
                {
                    "Name": "name",
                    "AttributeDataType": "String",
                    "Required": False,
                    "Mutable": True,
                },
            ],
            AdminCreateUserConfig={
                "AllowAdminCreateUserOnly": True,  # Only admins can create users
                "InviteMessageTemplate": {
                    "EmailSubject": "Your Admin Portal Account",
                    "EmailMessage": "Your username is {username} and temporary password is {####}. Please login and change your password.",
                },
            },
            AccountRecoverySetting={
                "RecoveryMechanisms": [
                    {"Priority": 1, "Name": "verified_email"},
                ]
            },
        )

        user_pool = response["UserPool"]
        print(f"  Created User Pool: {user_pool['Id']}")
        return user_pool

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceExistsException":
            print(f"  User Pool '{pool_name}' already exists.")
            # Try to find existing pool
            pools = cognito_client.list_user_pools(MaxResults=60)
            for pool in pools.get("UserPools", []):
                if pool["Name"] == pool_name:
                    return {"Id": pool["Id"], "Name": pool["Name"]}
            raise
        raise


def create_app_client(cognito_client, user_pool_id: str, client_name: str) -> dict:
    """Create an App Client for the User Pool (no secret for SPA)."""
    print(f"Creating App Client: {client_name}...")

    try:
        response = cognito_client.create_user_pool_client(
            UserPoolId=user_pool_id,
            ClientName=client_name,
            GenerateSecret=False,  # Required for browser-based SPA
            ExplicitAuthFlows=[
                "ALLOW_USER_PASSWORD_AUTH",
                "ALLOW_REFRESH_TOKEN_AUTH",
                "ALLOW_USER_SRP_AUTH",
            ],
            PreventUserExistenceErrors="ENABLED",
            SupportedIdentityProviders=["COGNITO"],
            RefreshTokenValidity=30,  # days
            AccessTokenValidity=1,  # hour
            IdTokenValidity=1,  # hour
            TokenValidityUnits={
                "AccessToken": "hours",
                "IdToken": "hours",
                "RefreshToken": "days",
            },
        )

        client = response["UserPoolClient"]
        print(f"  Created App Client: {client['ClientId']}")
        return client

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceExistsException":
            print(f"  App Client '{client_name}' already exists.")
            # Try to find existing client
            clients = cognito_client.list_user_pool_clients(
                UserPoolId=user_pool_id, MaxResults=60
            )
            for client in clients.get("UserPoolClients", []):
                if client["ClientName"] == client_name:
                    return {"ClientId": client["ClientId"], "ClientName": client["ClientName"]}
            raise
        raise


def create_admin_user(
    cognito_client,
    user_pool_id: str,
    username: str,
    email: str,
    temporary_password: str,
) -> dict:
    """Create an admin user with a temporary password."""
    print(f"Creating admin user: {username} ({email})...")

    try:
        response = cognito_client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,  # Use email as username since we use email login
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": username},
            ],
            TemporaryPassword=temporary_password,
            MessageAction="SUPPRESS",  # Don't send email, we'll show password in output
        )

        user = response["User"]
        print(f"  Created user: {user['Username']}")
        return user

    except ClientError as e:
        if e.response["Error"]["Code"] == "UsernameExistsException":
            print(f"  User '{email}' already exists.")
            return {"Username": email, "Existing": True}
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Setup AWS Cognito User Pool for Admin Portal"
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region (default: us-east-1)",
    )
    parser.add_argument(
        "--pool-name",
        default="anthropic-proxy-admin",
        help="User Pool name (default: anthropic-proxy-admin)",
    )
    parser.add_argument(
        "--client-name",
        default="anthropic-proxy-admin-client",
        help="App Client name (default: anthropic-proxy-admin-client)",
    )
    parser.add_argument(
        "--admin-email",
        required=True,
        help="Email address for the admin user",
    )
    parser.add_argument(
        "--admin-username",
        default="admin",
        help="Display name for the admin user (default: admin)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file for configuration JSON (default: stdout)",
    )

    # Default env file paths
    default_backend_env = Path(__file__).parent.parent / "backend" / ".env"
    default_frontend_env = Path(__file__).parent.parent / "frontend" / ".env"
    parser.add_argument(
        "--env-file",
        default=str(default_backend_env),
        help=f"Backend environment file (default: {default_backend_env})",
    )
    parser.add_argument(
        "--frontend-env-file",
        default=str(default_frontend_env),
        help=f"Frontend environment file (default: {default_frontend_env})",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Don't write to any environment files",
    )

    args = parser.parse_args()

    # Create Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=args.region)

    print("\n=== Setting up Cognito User Pool ===\n")

    # Create User Pool
    user_pool = create_user_pool(cognito_client, args.pool_name)
    user_pool_id = user_pool["Id"]

    # Create App Client
    app_client = create_app_client(cognito_client, user_pool_id, args.client_name)
    client_id = app_client["ClientId"]

    # Generate temporary password
    temporary_password = generate_temporary_password()

    # Create admin user
    admin_user = create_admin_user(
        cognito_client,
        user_pool_id,
        args.admin_username,
        args.admin_email,
        temporary_password,
    )

    # Prepare output configuration
    config = {
        "userPoolId": user_pool_id,
        "userPoolClientId": client_id,
        "region": args.region,
        "adminEmail": args.admin_email,
        "adminUsername": args.admin_username,
    }

    # Only include temporary password if user was newly created
    if not admin_user.get("Existing"):
        config["temporaryPassword"] = temporary_password

    # Output configuration
    config_json = json.dumps(config, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(config_json)
        print(f"\nConfiguration saved to: {args.output}")
    else:
        print("\n=== Configuration ===\n")
        print(config_json)

    # Write environment file (default: backend/.env)
    if args.env_file and not args.no_env_file:
        env_path = Path(args.env_file)

        # Read existing content if file exists
        existing_content = ""
        existing_vars = {}
        if env_path.exists():
            existing_content = env_path.read_text()
            # Parse existing variables
            for line in existing_content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key = line.split("=", 1)[0]
                    existing_vars[key] = True

        # Cognito variables to add/update
        cognito_vars = {
            "COGNITO_USER_POOL_ID": user_pool_id,
            "COGNITO_CLIENT_ID": client_id,
            "COGNITO_REGION": args.region,
        }

        # Add temporary password if user was newly created
        if not admin_user.get("Existing"):
            cognito_vars["COGNITO_ADMIN_EMAIL"] = args.admin_email
            cognito_vars["COGNITO_TEMP_PASSWORD"] = temporary_password

        # Update existing content or append new variables
        new_lines = []
        updated_vars = set()

        for line in existing_content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0]
                if key in cognito_vars:
                    new_lines.append(f"{key}={cognito_vars[key]}")
                    updated_vars.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Add any new variables that weren't in the file
        new_vars = set(cognito_vars.keys()) - updated_vars
        if new_vars:
            if new_lines and new_lines[-1].strip():  # Add blank line if needed
                new_lines.append("")
            new_lines.append("# Cognito Configuration (generated by setup_cognito.py)")
            for key in sorted(new_vars):
                new_lines.append(f"{key}={cognito_vars[key]}")

        # Write updated content
        env_path.write_text("\n".join(new_lines) + "\n")
        print(f"\nBackend configuration saved to: {env_path}")

    # Write frontend environment file
    if args.frontend_env_file and not args.no_env_file:
        frontend_env_path = Path(args.frontend_env_file)

        # Read existing content if file exists
        existing_content = ""
        if frontend_env_path.exists():
            existing_content = frontend_env_path.read_text()

        # Frontend variables (VITE_ prefix for Vite)
        frontend_vars = {
            "VITE_COGNITO_USER_POOL_ID": user_pool_id,
            "VITE_COGNITO_CLIENT_ID": client_id,
            "VITE_AWS_REGION": args.region,
        }

        # Update existing content or append new variables
        new_lines = []
        updated_vars = set()

        for line in existing_content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0]
                if key in frontend_vars:
                    new_lines.append(f"{key}={frontend_vars[key]}")
                    updated_vars.add(key)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Add any new variables that weren't in the file
        new_vars = set(frontend_vars.keys()) - updated_vars
        if new_vars:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append("# Cognito Configuration for Vite (generated by setup_cognito.py)")
            for key in sorted(new_vars):
                new_lines.append(f"{key}={frontend_vars[key]}")

        # Write updated content
        frontend_env_path.write_text("\n".join(new_lines) + "\n")
        print(f"Frontend configuration saved to: {frontend_env_path}")

    print("\n=== Setup Complete ===\n")
    if not admin_user.get("Existing"):
        print(f"Admin user created: {args.admin_email}")
        print(f"Temporary password: {temporary_password}")
        print("\nIMPORTANT: On first login, you will be required to set a new password.")
    else:
        print(f"Admin user already exists: {args.admin_email}")

    if not args.no_env_file:
        print("\nConfiguration files updated:")
        print(f"  - Backend:  {args.env_file}")
        print(f"  - Frontend: {args.frontend_env_file}")
        print("\nRestart both servers to apply changes.")
    else:
        print("\nTo configure manually:")
        print("\n1. Add to backend .env:")
        print(f"   COGNITO_USER_POOL_ID={user_pool_id}")
        print(f"   COGNITO_CLIENT_ID={client_id}")
        print(f"   COGNITO_REGION={args.region}")
        print("\n2. Add to frontend .env:")
        print(f"   VITE_COGNITO_USER_POOL_ID={user_pool_id}")
        print(f"   VITE_COGNITO_CLIENT_ID={client_id}")
        print(f"   VITE_AWS_REGION={args.region}")


if __name__ == "__main__":
    main()
