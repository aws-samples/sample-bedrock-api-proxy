/**
 * Authentication Hook using AWS Amplify Cognito
 *
 * Provides authentication state and methods for login, logout,
 * password change, and session management.
 *
 * Uses React Context to share auth state across all components.
 * Listens for auth error events from API service to handle session expiration.
 */
import { useState, useEffect, useCallback, createContext, useContext, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  signIn,
  signOut,
  getCurrentUser,
  fetchAuthSession,
  confirmSignIn,
  resetPassword,
  confirmResetPassword,
  AuthUser,
} from 'aws-amplify/auth';
import { isAmplifyConfigured } from '../config/amplify';
import { AUTH_ERROR_EVENT } from '../services/api';

export type AuthState = 'idle' | 'loading' | 'authenticated' | 'unauthenticated' | 'newPasswordRequired';
export type ResetPasswordState = 'idle' | 'codeSent' | 'success';

export interface AuthUserInfo {
  username: string;
  email?: string;
  name?: string;
  developmentMode?: boolean;
}

interface AuthContextValue {
  authState: AuthState;
  authenticated: boolean;
  user: AuthUserInfo | null;
  loading: boolean;
  error: string | null;
  isNewPasswordRequired: boolean;
  sessionExpired: boolean;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<boolean>;
  completeNewPassword: (newPassword: string) => Promise<boolean>;
  getAccessToken: () => Promise<string | null>;
  clearSessionExpired: () => void;
  // Reset password
  resetPasswordState: ResetPasswordState;
  resetPasswordUsername: string | null;
  initiateResetPassword: (username: string) => Promise<boolean>;
  completeResetPassword: (code: string, newPassword: string) => Promise<boolean>;
  cancelResetPassword: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authState, setAuthState] = useState<AuthState>('idle');
  const [user, setUser] = useState<AuthUserInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [resetPasswordState, setResetPasswordState] = useState<ResetPasswordState>('idle');
  const [resetPasswordUsername, setResetPasswordUsername] = useState<string | null>(null);
  const navigate = useNavigate();

  // Check current authentication status
  const checkAuth = useCallback(async (): Promise<boolean> => {
    // If Amplify is not configured, use development mode
    if (!isAmplifyConfigured()) {
      setUser({
        username: 'dev-user',
        email: 'dev@example.com',
        developmentMode: true,
      });
      setAuthState('authenticated');
      return true;
    }

    try {
      const currentUser: AuthUser = await getCurrentUser();
      const session = await fetchAuthSession();

      if (currentUser && session.tokens) {
        setUser({
          username: currentUser.username,
          email: currentUser.signInDetails?.loginId,
        });
        setAuthState('authenticated');
        return true;
      }
    } catch {
      // Not authenticated
    }

    setUser(null);
    setAuthState('unauthenticated');
    return false;
  }, []);

  // Initialize auth state on mount
  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Listen for auth error events from API service
  useEffect(() => {
    const handleAuthError = (event: Event) => {
      const customEvent = event as CustomEvent<{ reason: string }>;
      const reason = customEvent.detail?.reason;

      console.warn('Auth error received:', reason);

      // Clear user state
      setUser(null);
      setAuthState('unauthenticated');
      setSessionExpired(true);

      // Set appropriate error message
      const messages: Record<string, string> = {
        token_refresh_failed: 'Your session has expired. Please login again.',
        unauthorized: 'Your session is no longer valid. Please login again.',
        no_token: 'Authentication required. Please login.',
      };
      setError(messages[reason] || 'Session expired. Please login again.');

      // Sign out from Amplify (cleanup) and navigate to login
      if (isAmplifyConfigured()) {
        signOut().catch(() => {});
      }
      navigate('/login');
    };

    window.addEventListener(AUTH_ERROR_EVENT, handleAuthError);
    return () => {
      window.removeEventListener(AUTH_ERROR_EVENT, handleAuthError);
    };
  }, [navigate]);

  // Login with username and password
  const login = useCallback(
    async (username: string, password: string): Promise<boolean> => {
      setLoading(true);
      setError(null);

      // Development mode - skip Cognito
      if (!isAmplifyConfigured()) {
        setUser({
          username: 'dev-user',
          email: 'dev@example.com',
          developmentMode: true,
        });
        setAuthState('authenticated');
        setLoading(false);
        navigate('/dashboard');
        return true;
      }

      try {
        const result = await signIn({ username, password });

        if (result.isSignedIn) {
          // Successfully signed in
          const currentUser = await getCurrentUser();
          setUser({
            username: currentUser.username,
            email: currentUser.signInDetails?.loginId,
          });
          setAuthState('authenticated');
          navigate('/dashboard');
          return true;
        }

        // Handle challenges
        if (result.nextStep.signInStep === 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED') {
          setAuthState('newPasswordRequired');
          return false;
        }

        // Other steps not handled yet
        setError(`Unexpected sign in step: ${result.nextStep.signInStep}`);
        return false;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Login failed';
        setError(message);
        setAuthState('unauthenticated');
        return false;
      } finally {
        setLoading(false);
      }
    },
    [navigate]
  );

  // Complete new password challenge (first login with temporary password)
  const completeNewPassword = useCallback(
    async (newPassword: string): Promise<boolean> => {
      setLoading(true);
      setError(null);

      try {
        const result = await confirmSignIn({ challengeResponse: newPassword });

        if (result.isSignedIn) {
          const currentUser = await getCurrentUser();
          setUser({
            username: currentUser.username,
            email: currentUser.signInDetails?.loginId,
          });
          setAuthState('authenticated');
          navigate('/dashboard');
          return true;
        }

        setError('Failed to complete password change');
        return false;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Password change failed';
        setError(message);
        return false;
      } finally {
        setLoading(false);
      }
    },
    [navigate]
  );

  // Logout
  const logout = useCallback(async () => {
    setLoading(true);

    // Development mode
    if (!isAmplifyConfigured()) {
      setUser(null);
      setAuthState('unauthenticated');
      setLoading(false);
      navigate('/login');
      return;
    }

    try {
      await signOut();
    } catch (err) {
      console.error('Logout error:', err);
    }

    setUser(null);
    setAuthState('unauthenticated');
    setLoading(false);
    navigate('/login');
  }, [navigate]);

  // Get current access token for API calls
  const getAccessToken = useCallback(async (): Promise<string | null> => {
    // Development mode - no token needed
    if (!isAmplifyConfigured()) {
      return null;
    }

    try {
      const session = await fetchAuthSession();
      return session.tokens?.idToken?.toString() || null;
    } catch {
      return null;
    }
  }, []);

  // Clear session expired flag (call after showing message to user)
  const clearSessionExpired = useCallback(() => {
    setSessionExpired(false);
    setError(null);
  }, []);

  // Initiate password reset - sends verification code to user's email
  const initiateResetPassword = useCallback(async (username: string): Promise<boolean> => {
    // Development mode - no reset available
    if (!isAmplifyConfigured()) {
      setError('Password reset is not available in development mode');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const output = await resetPassword({ username });

      if (output.nextStep.resetPasswordStep === 'CONFIRM_RESET_PASSWORD_WITH_CODE') {
        setResetPasswordUsername(username);
        setResetPasswordState('codeSent');
        return true;
      }

      // Handle other possible steps
      setError(`Unexpected reset step: ${output.nextStep.resetPasswordStep}`);
      return false;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to send reset code';
      setError(message);
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  // Complete password reset with verification code and new password
  const completeResetPassword = useCallback(async (code: string, newPassword: string): Promise<boolean> => {
    if (!resetPasswordUsername) {
      setError('No reset password session found');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      await confirmResetPassword({
        username: resetPasswordUsername,
        confirmationCode: code,
        newPassword,
      });

      setResetPasswordState('success');
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to reset password';
      setError(message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [resetPasswordUsername]);

  // Cancel reset password flow and return to login
  const cancelResetPassword = useCallback(() => {
    setResetPasswordState('idle');
    setResetPasswordUsername(null);
    setError(null);
  }, []);

  const value: AuthContextValue = {
    authState,
    authenticated: authState === 'authenticated',
    user,
    loading,
    error,
    isNewPasswordRequired: authState === 'newPasswordRequired',
    sessionExpired,
    login,
    logout,
    checkAuth,
    completeNewPassword,
    getAccessToken,
    clearSessionExpired,
    resetPasswordState,
    resetPasswordUsername,
    initiateResetPassword,
    completeResetPassword,
    cancelResetPassword,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
