/**
 * AWS Amplify Configuration for Admin Portal
 *
 * Configures Amplify to use AWS Cognito for authentication.
 * Configuration is fetched from the backend /api/auth/config endpoint,
 * with fallback to environment variables if backend is unavailable.
 */
import { Amplify } from 'aws-amplify';

export interface CognitoConfig {
  userPoolId: string;
  userPoolClientId: string;
  region: string;
}

/**
 * Fetch Cognito configuration from the backend API.
 */
export async function fetchCognitoConfig(): Promise<CognitoConfig | null> {
  try {
    const response = await fetch('/api/auth/config');
    if (response.ok) {
      return await response.json();
    }
    console.warn('Failed to fetch Cognito config from backend');
    return null;
  } catch (error) {
    console.warn('Error fetching Cognito config:', error);
    return null;
  }
}

/**
 * Get Cognito configuration from environment variables (fallback).
 */
function getEnvConfig(): CognitoConfig | null {
  const userPoolId = import.meta.env.VITE_COGNITO_USER_POOL_ID;
  const userPoolClientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
  const region = import.meta.env.VITE_AWS_REGION || 'us-east-1';

  if (userPoolId && userPoolClientId) {
    return { userPoolId, userPoolClientId, region };
  }
  return null;
}

/**
 * Configure Amplify with the provided Cognito configuration.
 */
export function configureAmplify(config: CognitoConfig): void {
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolClientId,
      },
    },
  });

  console.log('Amplify configured with Cognito User Pool');
}

/**
 * Initialize Amplify - tries backend config first, falls back to env vars.
 * Returns true if Cognito is configured, false if running in development mode.
 */
export async function initializeAmplify(): Promise<boolean> {
  // Try to get config from backend first
  let config = await fetchCognitoConfig();

  // If backend config is empty or unavailable, try env vars
  if (!config || !config.userPoolId) {
    config = getEnvConfig();
  }

  // If we have valid config, configure Amplify
  if (config && config.userPoolId && config.userPoolClientId) {
    configureAmplify(config);
    return true;
  }

  // No Cognito configuration - running in development mode
  console.warn(
    'No Cognito configuration found. Running in development mode without authentication.'
  );
  return false;
}

// Store whether Amplify is configured
let amplifyConfigured = false;

export function isAmplifyConfigured(): boolean {
  return amplifyConfigured;
}

export function setAmplifyConfigured(configured: boolean): void {
  amplifyConfigured = configured;
}
