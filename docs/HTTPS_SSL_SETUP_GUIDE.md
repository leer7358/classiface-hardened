# ClassiFace - Production HTTPS & SSL Configuration Guide

## Overview

This guide provides step-by-step instructions for configuring ClassiFace with production-grade HTTPS and SSL/TLS security using a reverse proxy (nginx or Apache). All HTTP traffic will be automatically redirected to HTTPS, and HSTS headers will be enforced for enhanced security.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [SSL Certificate Setup](#ssl-certificate-setup)
3. [nginx Configuration](#nginx-configuration)
4. [Apache Configuration](#apache-configuration)
5. [Flask Application Configuration](#flask-application-configuration)
6. [Security Testing](#security-testing)
7. [Troubleshooting](#troubleshooting)
8. [Maintenance](#maintenance)

---

## Architecture Overview

### How It Works

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │
       │ HTTP:80 (Redirect)
       ├──────────────────→ HTTPS:443
       │
       │ HTTPS:443 (Encrypted)
       │
       ▼
┌──────────────────────────┐
│   nginx/Apache (SSL)     │
│   - HTTP → HTTPS        │
│   - SSL Termination     │
│   - Reverse Proxy       │
│   - HSTS Headers        │
└──────────────┬───────────┘
               │
               │ HTTP:5000 (Internal)
               │
               ▼
        ┌─────────────────┐
        │  Flask App      │
        │  (127.0.0.1:5000)
        │  - HSTS Header  │
        │  - CSP Header   │
        │  - Security     │
        └─────────────────┘
```

### Key Points

- **Reverse Proxy**: nginx or Apache handles HTTPS, terminates SSL
- **HTTP Redirect**: All HTTP requests (port 80) are redirected to HTTPS (port 443)
- **HSTS**: HTTP Strict Transport Security header enforces HTTPS for 1 year
- **Backend**: Flask app runs on localhost:5000 (HTTP only) - safe on internal network

---

## SSL Certificate Setup

### Option 1: Let's Encrypt (Recommended - Free)

Let's Encrypt provides free SSL certificates. We'll use Certbot for automated setup and renewal.

#### Step 1: Install Certbot

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install certbot python3-certbot-nginx  # For nginx
# OR
sudo apt install certbot python3-certbot-apache  # For Apache
```

**CentOS/RHEL:**
```bash
sudo dnf install certbot python3-certbot-nginx  # For nginx
# OR
sudo dnf install certbot python3-certbot-apache  # For Apache
```

#### Step 2: Obtain SSL Certificate

**For nginx:**
```bash
sudo certbot certonly --webroot -w /var/www/classiface -d your-domain.com -d www.your-domain.com
```

**For Apache:**
```bash
sudo certbot certonly --webroot -w /var/www/classiface -d your-domain.com -d www.your-domain.com
```

Replace:
- `your-domain.com` with your actual domain
- `/var/www/classiface` with your web root path

#### Step 3: Verify Certificate Installation

Certificates are located at:
```
/etc/letsencrypt/live/your-domain.com/
├── fullchain.pem      (Full certificate chain)
├── privkey.pem        (Private key)
├── cert.pem           (Certificate only)
└── chain.pem          (Intermediate CA certificates)
```

### Option 2: Commercial SSL Certificate

If using a commercial SSL provider (DigiCert, GeoTrust, Comodo, etc.):

1. Purchase and download your SSL certificate files
2. Place files in a secure directory (e.g., `/etc/ssl/classiface/`)
3. Update configuration files with correct paths:
   - `ssl_certificate`: Full certificate chain path
   - `ssl_certificate_key`: Private key path

---

## nginx Configuration

### Step 1: Update Configuration File

1. Copy the provided `nginx.conf` template
2. Update the following placeholders:

```bash
# Update domain names
server_name your-domain.com www.your-domain.com *.your-domain.com;

# Update SSL certificate paths (if not using Let's Encrypt)
ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

# Update backend application port (if not 5000)
upstream classiface_app {
    server 127.0.0.1:5000;
    keepalive 32;
}

# Update static files path
location /static/ {
    alias /path/to/classiface/static/;
    expires 30d;
    add_header Cache-Control "public, immutable";
}
```

### Step 2: Install Configuration

```bash
# Copy configuration to nginx sites-available
sudo cp nginx.conf /etc/nginx/sites-available/classiface

# Create symlink to sites-enabled
sudo ln -s /etc/nginx/sites-available/classiface /etc/nginx/sites-enabled/classiface

# Disable default site (optional)
sudo rm /etc/nginx/sites-enabled/default

# Test configuration for syntax errors
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

### Step 3: Verify nginx Status

```bash
sudo systemctl status nginx
sudo systemctl enable nginx  # Enable auto-start on boot
```

---

## Apache Configuration

### Step 1: Enable Required Modules

```bash
sudo a2enmod ssl
sudo a2enmod rewrite
sudo a2enmod proxy
sudo a2enmod proxy_http
sudo a2enmod proxy_wstunnel
sudo a2enmod headers
sudo a2enmod expires
sudo a2enmod deflate
```

### Step 2: Update Configuration File

1. Copy the provided `apache.conf` template
2. Update the following placeholders:

```bash
# Update domain names
ServerName your-domain.com
ServerAlias www.your-domain.com

# Update SSL certificate paths (if not using Let's Encrypt)
SSLCertificateFile /etc/letsencrypt/live/your-domain.com/fullchain.pem
SSLCertificateKeyFile /etc/letsencrypt/live/your-domain.com/privkey.pem
SSLCertificateChainFile /etc/letsencrypt/live/your-domain.com/chain.pem

# Update backend application port (if not 5000)
ProxyPass / http://127.0.0.1:5000/ timeout=600 connectiontimeout=60
ProxyPassReverse / http://127.0.0.1:5000/

# Update static files path
<Directory /path/to/classiface/static>
    ...
</Directory>
```

### Step 3: Install Configuration

```bash
# Copy configuration to Apache sites-available
sudo cp apache.conf /etc/apache2/sites-available/classiface.conf

# Enable site
sudo a2ensite classiface

# Disable default site (optional)
sudo a2dissite 000-default

# Test configuration for syntax errors
sudo apache2ctl configtest

# If output is "Syntax OK", restart Apache
sudo systemctl restart apache2
```

### Step 4: Verify Apache Status

```bash
sudo systemctl status apache2
sudo systemctl enable apache2  # Enable auto-start on boot
```

---

## Flask Application Configuration

### HSTS Header Configuration

The Flask application (`app.py`) has been configured with HSTS headers:

```python
response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
```

**Configuration Details:**
- `max-age=31536000`: HSTS enforced for 1 year (31536000 seconds)
- `includeSubDomains`: All subdomains must use HTTPS
- **⚠️ CRITICAL**: Only enable HSTS after SSL certificate is properly configured
- Once set, browsers will cache this policy - incorrect configuration can lock users out

### Session Cookie Security

The following security settings are already configured in `app.py`:

```python
SESSION_COOKIE_HTTPONLY=True,      # Prevents JavaScript access
SESSION_COOKIE_SAMESITE="Lax",     # CSRF protection
SESSION_COOKIE_SECURE=bool(os.environ.get("HTTPS", "")),  # HTTPS only
```

### Other Security Headers (Already Configured)

- **Content-Security-Policy**: Restricts script sources, prevents XSS
- **X-Frame-Options**: Prevents clickjacking (SAMEORIGIN)
- **X-Content-Type-Options**: Prevents MIME sniffing (nosniff)
- **Referrer-Policy**: Limits referrer information sharing

---

## Security Testing

### Test HTTP to HTTPS Redirect

```bash
# Test direct HTTP access - should redirect to HTTPS
curl -I http://your-domain.com
# Expected response: 301 Moved Permanently
# Location: https://your-domain.com/

# Test with -L flag to follow redirects
curl -L http://your-domain.com
# Should successfully load HTTPS content
```

### Test HSTS Header

```bash
# Check for HSTS header in response
curl -I https://your-domain.com
# Should include header:
# Strict-Transport-Security: max-age=31536000; includeSubDomains
```

### Test SSL/TLS Configuration

```bash
# Online SSL testing tools:
# 1. https://www.ssllabs.com/ssltest/
#    Enter your domain and get detailed SSL security report
# 2. https://www.testssl.sh/
#    Download and run TestSSL.sh locally for comprehensive testing

# Or use OpenSSL locally:
openssl s_client -connect your-domain.com:443 -tls1_2
openssl s_client -connect your-domain.com:443 -tls1_3
```

### Test Security Headers

```bash
# Use online tools:
# https://securityheaders.com
# Enter your domain to get a security headers report

# Or check with curl:
curl -I https://your-domain.com | grep -i "security\|hsts\|csp"
```

### Test WebSocket Connection (Socket.IO)

```bash
# In browser console, test Socket.IO connection
# Should work over HTTPS:
# Socket.IO will automatically upgrade to WSS (WebSocket Secure)

# Check logs:
curl -I https://your-domain.com/socket.io/
```

---

## Troubleshooting

### Issue: Redirect Loop (Too Many Redirects)

**Cause**: Application and reverse proxy both redirecting to HTTPS

**Solution**:
1. Ensure Flask app doesn't have HTTPS redirect logic (it shouldn't)
2. Verify reverse proxy is properly configured
3. Check `X-Forwarded-Proto` header is being set correctly

```bash
curl -v https://your-domain.com
# Check response headers for correct forwarding
```

### Issue: Mixed Content Warning (ERR_BLOCKED_BY_CLIENT)

**Cause**: JavaScript trying to load resources over HTTP

**Solution**:
1. Update CSP header to force HTTPS resources
2. Ensure all external resources use `https://`
3. Update Flask template to use protocol-relative URLs: `//cdn.example.com/...`

### Issue: SSL Certificate Not Found

**Cause**: Certificate path incorrect or Let's Encrypt renewal failed

**Solution**:
```bash
# Verify certificate exists
ls -la /etc/letsencrypt/live/your-domain.com/

# Test Let's Encrypt renewal
sudo certbot renew --dry-run

# If renewal fails, debug
sudo certbot renew --verbose
```

### Issue: 502 Bad Gateway (nginx) or 502 Proxy Error (Apache)

**Cause**: Flask application not running or unreachable

**Solution**:
```bash
# Verify Flask app is running
ps aux | grep python
# Should see your Flask app process

# Check if port 5000 is listening
netstat -tulpn | grep 5000
# Should see: tcp  0  0 127.0.0.1:5000

# Check reverse proxy logs
sudo tail -f /var/log/nginx/classiface.error.log      # nginx
sudo tail -f /var/log/apache2/classiface-https.error.log  # Apache

# Test connectivity to backend
curl http://127.0.0.1:5000/
```

### Issue: WebSocket Connection Failures

**Cause**: Socket.IO proxy not configured correctly

**Solution**:
1. Verify `proxy_pass` for `/socket.io` endpoint
2. Ensure upgrade headers are set: `Upgrade: websocket`
3. Check connection timeout isn't too short

```bash
# nginx: verify /socket.io location block exists
# Apache: verify mod_proxy_wstunnel is enabled (sudo a2enmod proxy_wstunnel)

# Test WebSocket connection
curl -I -H "Upgrade: websocket" https://your-domain.com/socket.io/
```

### Issue: HSTS Errors in Browser

**Cause**: HSTS previously set with invalid configuration

**Solution** (One-time fix):
1. Clear browser HSTS cache:
   - Chrome: `chrome://net-internals/#hsts`
   - Firefox: Clear HSTS settings or use a different browser
2. Fix server configuration
3. Re-test

---

## Maintenance

### Automatic Certificate Renewal

Let's Encrypt certificates expire after 90 days. Certbot provides automatic renewal.

#### Setup Auto-Renewal Cron Job

```bash
# Edit crontab
sudo crontab -e

# Add this line for daily renewal check (runs at 3 AM)
0 3 * * * /usr/bin/certbot renew --quiet && systemctl reload nginx

# For Apache:
0 3 * * * /usr/bin/certbot renew --quiet && systemctl reload apache2
```

#### Manual Renewal

```bash
# Force renewal (even if not due)
sudo certbot renew --force-renewal

# Verify renewal status
sudo certbot renew --dry-run
```

#### Check Renewal Schedule

```bash
# List upcoming renewals
sudo certbot certificates

# Check logs
sudo tail -f /var/log/letsencrypt/letsencrypt.log
```

### Monitor Certificate Expiration

```bash
# Check certificate expiration date
echo | openssl s_client -servername your-domain.com -connect your-domain.com:443 2>/dev/null | openssl x509 -noout -dates

# Set calendar reminder for 30 days before expiration
# Or rely on Certbot notifications (email notifications can be configured)
```

### Log Rotation

Configure logrotate to manage large log files:

```bash
# nginx
sudo nano /etc/logrotate.d/nginx

# Apache  
sudo nano /etc/logrotate.d/apache2
```

### Update Security Policies Regularly

Review and update:
- TLS/SSL protocol versions (deprecate older versions as needed)
- Cipher suites (follow OWASP recommendations)
- CSP policy (as new endpoints are added)
- HSTS preload list (optional, but recommended for production)

---

## HSTS Preload (Optional but Recommended)

Once your HTTPS setup is stable and verified, you can add your domain to the HSTS preload list:

1. Ensure HSTS header is properly set: `max-age=31536000; includeSubDomains; preload`
2. Visit https://hstspreload.org/
3. Submit your domain for inclusion
4. Verify domain ownership
5. Once approved, browsers will enforce HTTPS for your domain even on first visit

---

## Quick Reference

### Check HSTS Header

```bash
curl -I https://your-domain.com | grep -i "strict"
```

### Restart Services

```bash
# nginx
sudo systemctl restart nginx

# Apache
sudo systemctl restart apache2
```

### View Error Logs

```bash
# nginx
sudo tail -f /var/log/nginx/classiface.error.log

# Apache
sudo tail -f /var/log/apache2/classiface-https.error.log
```

### Test Application Health

```bash
# Via reverse proxy (HTTPS)
curl https://your-domain.com/health

# Directly to Flask (HTTP, local only)
curl http://127.0.0.1:5000/health
```

---

## Additional Resources

- **nginx**: https://nginx.org/en/docs/
- **Apache**: https://httpd.apache.org/docs/
- **Let's Encrypt**: https://letsencrypt.org/docs/
- **OWASP Security Headers**: https://owasp.org/www-project-secure-headers/
- **Mozilla SSL Configuration**: https://ssl-config.mozilla.org/
- **HSTS**: https://tools.ietf.org/html/rfc6797

---

## Support

For issues or questions:
1. Check the Troubleshooting section above
2. Review reverse proxy error logs
3. Run SSL test tools (SSLLabs, TestSSL)
4. Consult reverse proxy documentation
