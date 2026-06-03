/**
 * Password Strength Checker - Real-time password validation feedback
 * 
 * Usage in HTML:
 * 1. Add data-password-strength attribute to password input: 
 *    <input type="password" id="new_password" data-password-strength>
 * 
 * 2. Add a strength indicator container:
 *    <div id="password_strength_indicator" style="display:none; margin-top:8px;"></div>
 * 
 * 3. Load this script:
 *    <script src="{{ url_for('static', filename='password-strength.js') }}"></script>
 */

class PasswordStrengthChecker {
    constructor() {
        this.strengthLevels = {
            0: { label: 'Very Weak', color: '#ff6b84', bgColor: 'rgba(255,107,132,0.1)' },
            1: { label: 'Weak', color: '#ff9f43', bgColor: 'rgba(255,159,67,0.1)' },
            2: { label: 'Fair', color: '#ffc107', bgColor: 'rgba(255,193,7,0.1)' },
            3: { label: 'Good', color: '#26de81', bgColor: 'rgba(38,222,129,0.1)' },
            4: { label: 'Very Strong', color: '#2bcf6b', bgColor: 'rgba(43,207,107,0.1)' }
        };
        
        this.init();
    }
    
    init() {
        // Find all password inputs with data-password-strength attribute
        document.querySelectorAll('input[type="password"][data-password-strength]').forEach(input => {
            input.addEventListener('input', (e) => this.checkPassword(e.target));
        });
    }
    
    async checkPassword(passwordInput) {
        const password = passwordInput.value;
        const indicatorId = passwordInput.getAttribute('data-indicator-id') || 
                           'password_strength_indicator';
        const indicator = document.getElementById(indicatorId);
        
        if (!password) {
            if (indicator) indicator.style.display = 'none';
            return;
        }
        
        try {
            const response = await fetch('/api/password/check-strength', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password })
            });
            
            const data = await response.json();
            this.displayStrengthIndicator(indicator, data);
            
        } catch (error) {
            console.error('Password strength check failed:', error);
            if (indicator) {
                indicator.innerHTML = `<div style="color:#ff6b84; font-size:12px;">Unable to validate password</div>`;
                indicator.style.display = 'block';
            }
        }
    }
    
    displayStrengthIndicator(indicator, data) {
        if (!indicator) return;
        
        const strength = data.strength || 0;
        const level = this.strengthLevels[strength];
        const isValid = data.valid;
        
        let html = `
            <div style="background:${level.bgColor}; padding:12px; border-radius:8px; border-left:4px solid ${level.color};">
                <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                    <div style="flex:1; height:6px; background:rgba(255,255,255,0.2); border-radius:3px; overflow:hidden;">
                        <div style="height:100%; width:${(strength + 1) * 20}%; background:${level.color}; transition:width 0.3s;"></div>
                    </div>
                    <div style="font-size:12px; font-weight:500; color:${level.color};">
                        ${level.label}
                    </div>
                </div>
        `;
        
        if (data.errors && data.errors.length > 0) {
            html += `<div style="font-size:12px; color:#ff6b84; margin-bottom:6px;">`;
            data.errors.forEach(error => {
                html += `<div style="margin:3px 0;">❌ ${this.escapeHtml(error)}</div>`;
            });
            html += `</div>`;
        }
        
        if (isValid) {
            html += `
                <div style="font-size:12px; color:#26de81; font-weight:500;">
                    ✓ Password meets all requirements
                </div>
            `;
        } else if (data.errors && data.errors.length > 0) {
            html += `
                <div style="font-size:11px; color:#98a5b8; margin-top:6px;">
                    Please fix the requirements above
                </div>
            `;
        }
        
        html += `</div>`;
        
        indicator.innerHTML = html;
        indicator.style.display = 'block';
    }
    
    escapeHtml(text) {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, m => map[m]);
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        new PasswordStrengthChecker();
    });
} else {
    new PasswordStrengthChecker();
}
