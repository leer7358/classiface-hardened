# Production HTTPS/HSTS Deployment Checklist

## Pre-Deployment (Development/Staging)

### Code Changes
- [x] Flask app updated with HSTS header including `includeSubDomains`
- [x] Session cookies configured as secure: `SESSION_COOKIE_SECURE=True`
- [x] Session cookies configured as HTTP-only: `SESSION_COOKIE_HTTPONLY=True`
- [ ] All environment variables documented in `.env` template
- [ ] HTTPS environment variable documented (`HTTPS=true`)

### Configuration Files
- [x] nginx configuration created with HTTP→HTTPS redirect
- [x] Apache configuration created with HTTP→HTTPS redirect
- [ ] Select which reverse proxy to use (nginx or Apache)
- [ ] Review and customize selected configuration file
- [ ] Update domain names in configuration
- [ ] Update backend app port if not 5000
- [ ] Update static files path if different

### Security Review
- [ ] Review HSTS header policy (max-age, includeSubDomains)
- [ ] Review CSP policy restrictions
- [ ] Verify HTTPS environment variable will be set in production
- [ ] Test security headers in staging environment
- [ ] Run SSL Labs test in staging: https://www.ssllabs.com/ssltest/

---

## SSL Certificate Setup (Choose One)

### Let's Encrypt (Recommended)

- [ ] SSH into production server
- [ ] Install Certbot: `sudo apt install certbot python3-certbot-nginx` (or apache)
- [ ] Obtain certificate: `sudo certbot certonly --webroot -w /var/www/classiface -d your-domain.com -d www.your-domain.com`
- [ ] Verify certificate in: `/etc/letsencrypt/live/your-domain.com/`
- [ ] Configure auto-renewal cron job
- [ ] Test renewal: `sudo certbot renew --dry-run`
- [ ] Verify renewal will trigger: `sudo systemctl enable certbot.timer` (Ubuntu 20.04+)

### Commercial Certificate

- [ ] Obtain certificate from provider (DigiCert, GeoTrust, Comodo, etc.)
- [ ] Download certificate files
- [ ] Secure certificate directory: `/etc/ssl/classiface/` (600 permissions)
- [ ] Verify certificate chain integrity
- [ ] Document certificate expiration date and renewal process
- [ ] Set renewal reminder (30-60 days before expiration)

---

## Reverse Proxy Installation

### For nginx

- [ ] SSH into production server
- [ ] Install nginx: `sudo apt install nginx`
- [ ] Copy configuration: `sudo cp nginx.conf /etc/nginx/sites-available/classiface`
- [ ] Update domain names in `/etc/nginx/sites-available/classiface`
- [ ] Update SSL certificate paths
- [ ] Update backend port (if not 5000)
- [ ] Update static files path
- [ ] Create symlink: `sudo ln -s /etc/nginx/sites-available/classiface /etc/nginx/sites-enabled/classiface`
- [ ] Remove default site: `sudo rm /etc/nginx/sites-enabled/default`
- [ ] Test configuration: `sudo nginx -t`
- [ ] Start nginx: `sudo systemctl start nginx`
- [ ] Enable auto-start: `sudo systemctl enable nginx`
- [ ] Verify status: `sudo systemctl status nginx`

### For Apache

- [ ] SSH into production server
- [ ] Install Apache: `sudo apt install apache2`
- [ ] Enable required modules:
  - [ ] `sudo a2enmod ssl`
  - [ ] `sudo a2enmod rewrite`
  - [ ] `sudo a2enmod proxy`
  - [ ] `sudo a2enmod proxy_http`
  - [ ] `sudo a2enmod proxy_wstunnel`
  - [ ] `sudo a2enmod headers`
  - [ ] `sudo a2enmod expires`
  - [ ] `sudo a2enmod deflate`
- [ ] Copy configuration: `sudo cp apache.conf /etc/apache2/sites-available/classiface.conf`
- [ ] Update domain names
- [ ] Update SSL certificate paths
- [ ] Update backend port (if not 5000)
- [ ] Update static files path
- [ ] Enable site: `sudo a2ensite classiface`
- [ ] Disable default: `sudo a2dissite 000-default`
- [ ] Test configuration: `sudo apache2ctl configtest` (should output "Syntax OK")
- [ ] Start Apache: `sudo systemctl start apache2`
- [ ] Enable auto-start: `sudo systemctl enable apache2`
- [ ] Verify status: `sudo systemctl status apache2`

---

## Firewall Configuration

- [ ] Open HTTP port 80: `sudo ufw allow 80/tcp` (or firewall equivalent)
- [ ] Open HTTPS port 443: `sudo ufw allow 443/tcp`
- [ ] Close direct Flask app port 5000 from external access (internal only)
- [ ] Verify firewall rules: `sudo ufw status`

---

## Deployment Day

### Pre-Deployment Checks
- [ ] Database backup created
- [ ] Application code committed and tagged for release
- [ ] `.env` file configured with `HTTPS=true`
- [ ] `.env` file configured with all required secrets
- [ ] Flask app configured to run on 127.0.0.1:5000 (localhost only)
- [ ] Systemd service file created for Flask app auto-start (optional)

### Deploy Flask Application
- [ ] SSH into production server
- [ ] Clone or update application code
- [ ] Install/update Python dependencies: `pip install -r requirements.txt`
- [ ] Copy `.env` file (with secrets) to app directory
- [ ] Verify database connectivity
- [ ] Test Flask app locally: `python app.py` (if running directly)
- [ ] Ensure app is running on 127.0.0.1:5000

### Start Services
- [ ] Start Flask application
- [ ] Start reverse proxy (nginx or Apache)
- [ ] Verify both services are running

### Post-Deployment Verification
- [ ] Test HTTP redirect: `curl -I http://your-domain.com`
  - Expected: `301 Moved Permanently` to `https://your-domain.com`
- [ ] Test HTTPS access: `curl -I https://your-domain.com`
  - Expected: `200 OK` with proper security headers
- [ ] Verify HSTS header present: 
  - `curl -I https://your-domain.com | grep -i strict`
  - Expected: `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- [ ] Test application functionality
- [ ] Test WebSocket connections (Socket.IO)
- [ ] Check logs for errors:
  - nginx: `sudo tail -f /var/log/nginx/classiface.error.log`
  - Apache: `sudo tail -f /var/log/apache2/classiface-https.error.log`

### SSL Certificate Verification
- [ ] Run SSL Labs test: https://www.ssllabs.com/ssltest/
- [ ] Expected rating: A or higher
- [ ] Verify certificate chain is complete
- [ ] Check for warnings or vulnerabilities

### Security Headers Verification
- [ ] Check security headers: https://securityheaders.com
- [ ] Verify all expected headers are present
- [ ] Review CSP policy is working correctly

### Browser Testing
- [ ] Test in Chrome: HTTP redirect works, HTTPS loads
- [ ] Test in Firefox: HTTP redirect works, HTTPS loads  
- [ ] Test in Safari: HTTP redirect works, HTTPS loads
- [ ] Test with developer tools: Mixed content warning should not appear
- [ ] Test in browser console: Socket.IO connection successful

### Performance Testing
- [ ] Check page load times (should be minimal difference)
- [ ] Verify CSS/JS/images loading correctly
- [ ] Test file uploads/downloads over HTTPS
- [ ] Monitor resource usage: CPU, memory, network

---

## Post-Deployment (Next 48 Hours)

- [ ] Monitor error logs for anomalies
- [ ] Check traffic patterns for unusual activity
- [ ] Verify certificate renewal is scheduled and working
- [ ] Monitor reverse proxy performance
- [ ] Collect user feedback on HTTPS migration
- [ ] Document any issues encountered

---

## Ongoing Maintenance

### Weekly
- [ ] Check reverse proxy error logs
- [ ] Verify SSL certificate is valid
- [ ] Monitor application uptime

### Monthly
- [ ] Review security headers configuration
- [ ] Check for TLS/SSL vulnerabilities (CVEs)
- [ ] Review and analyze access logs for security patterns

### Quarterly
- [ ] Run full SSL Labs test
- [ ] Update TLS/SSL protocols if needed (deprecate older versions)
- [ ] Review and update CSP policy
- [ ] Penetration testing (optional but recommended)

### Annually
- [ ] Review and renew SSL certificate (if not Let's Encrypt auto-renewal)
- [ ] Consider HSTS preload list inclusion
- [ ] Update security policies based on latest OWASP recommendations
- [ ] Full security audit

---

## Rollback Plan (If Issues Occur)

If critical issues arise:

1. **Immediate**: Revert HTTP/HTTPS redirect to allow HTTP-only access
   - nginx: Comment out HTTP redirect in config, reload
   - Apache: Comment out RewriteCond/RewriteRule, restart
   
2. **Disable HSTS** (if browser caching issues)
   - Update app.py to remove HSTS header
   - Reload application
   - Users will need to clear HSTS cache in browsers

3. **Disable Reverse Proxy** (fall back to direct access)
   - Stop reverse proxy service
   - Ensure Flask app is accessible on 127.0.0.1:5000
   - Update DNS to point directly to app server (not recommended for production)

4. **Communication**
   - Notify users of the issue and ETA for fix
   - Update status page if available
   - Post incident report after resolution

---

## Important Notes

⚠️ **CRITICAL WARNINGS**:

1. **HSTS is Permanent**: Once set with `max-age=31536000`, browsers cache it for 1 year. Invalid SSL configuration will lock users out for that period.

2. **Test Thoroughly**: Verify HTTPS works completely before enabling HSTS in production.

3. **Certificate Renewal**: Let's Encrypt certificates expire every 90 days. Set up auto-renewal and monitor it.

4. **No HTTP Fallback**: After deploying, ensure HTTP→HTTPS redirect is working. Users with HSTS cached should never see HTTP again.

5. **Environment Variable**: Ensure `HTTPS=true` is set in production `.env` for proper security cookie configuration.

✓ **SUCCESS CRITERIA**:
- All HTTP requests redirect to HTTPS (301)
- HTTPS connection is secure (A+ rating on SSL Labs)
- All security headers present and correct
- WebSocket connections work (Socket.IO over WSS)
- Application functionality intact
- No console warnings about mixed content
- Performance acceptable

---

## Contact & Support

For deployment assistance or issues:
1. Review [HTTPS_SSL_SETUP_GUIDE.md](HTTPS_SSL_SETUP_GUIDE.md)
2. Check reverse proxy error logs
3. Run SSL test tools (SSLLabs, TestSSL)
4. Verify configuration files match environment
