# Importation des modules nÃ©cessaires
import os
import json
import subprocess
import requests
import xml.etree.ElementTree as ET
import re
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from functools import wraps

# Initialisation de l'application Flask
app = Flask(__name__)
app.secret_key = 'change_this_secret_key'   # ClÃ© secrÃ¨te pour les sessions (Ã  modifier en prod)

# DÃ©finition des chemins de fichiers
BASE_DIR = os.path.dirname(os.path.abspath(__file__))             # Dossier courant
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')               # Fichier de configuration JSON
NGINX_TEMPLATE = os.path.join(BASE_DIR, 'nginx-template.conf')    # Template Nginx
NGINX_OUTPUT = '/etc/nginx/nginx.conf'                            # Emplacement du fichier nginx.conf final

# Utilisateur(s) autorisÃ©(s) (Ã  remplacer par une base de donnÃ©es ou un hash en production)
USERS = {'admin': 'password'}

# DÃ©corateur : redirige vers /login si l'utilisateur n'est pas connectÃ©
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

# Route de connexion
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        if username in USERS and USERS[username] == request.form['password']:
            session['logged_in'] = True
            session['username'] = username
            return redirect('/')
    return render_template('login.html')

# Route de dÃ©connexion
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# Page d'accueil (interface principale)
@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    config = load_config()                                        # Charger la config actuelle
    stats = get_nginx_stats()                                     # RÃ©cupÃ©rer les statistiques RTMP
    if request.method == 'POST':
        # Mise Ã  jour des plateformes activÃ©es / URL / clÃ©
        for name in config['outputs']:
            config['outputs'][name]['enabled'] = f'enabled_{name}' in request.form
            config['outputs'][name]['url'] = request.form.get(f'url_{name}', '')
            config['outputs'][name]['key'] = request.form.get(f'key_{name}', '')
        save_config(config)                                       # Sauvegarder
        update_nginx_config(config)                               # Mettre Ã  jour nginx.conf
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx']) # RedÃ©marrer nginx
    return render_template('index.html',
                           platforms=config['outputs'],
                           username=session.get('username'),
                           stats=stats)

# Route pour supprimer une plateforme
@app.route('/delete/<platform>', methods=['POST'])
@login_required
def delete_platform(platform):
    config = load_config()
    if platform in config['outputs']:
        del config['outputs'][platform]                           # Supprimer l'entrÃ©e
        save_config(config)
        update_nginx_config(config)
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'])
    return redirect('/')

# Route pour ajouter une plateforme
@app.route('/add', methods=['POST'])
@login_required
def add_platform():
    config = load_config()
    name = request.form['new_name']
    url = request.form['new_url']
    key = request.form['new_key']
    if name and url and key:
        config['outputs'][name] = {
            'enabled': True,
            'url': url,
            'key': key
        }
        save_config(config)
        update_nginx_config(config)
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'])
    return redirect('/')

# Lecture du fichier de configuration
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {'outputs': {}}                                     # Valeur par dÃ©faut
    with open(CONFIG_FILE) as f:
        return json.load(f)

# Sauvegarde de la configuration dans le fichier JSON
def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

# Met Ã  jour le fichier nginx.conf en fonction de la configuration
def update_nginx_config(config):
    with open(NGINX_TEMPLATE) as f:
        template = f.read()

    # GÃ©nÃ©rer les lignes de push en fonction des plateformes
    push_lines = ""
    for name, data in config['outputs'].items():
        if data.get('enabled'):
            # GÃ©nÃ¨re la ligne `push` correcte pour chaque plateforme
            push_lines += f"push {data['url']}{data['key']};\n        "

    # Remplacer le placeholder `{{PUSH_STREAMS}}` dans le template Nginx par les bonnes lignes de push
    new_config = template.replace("{{PUSH_STREAMS}}", push_lines)

    # Ã‰crire la nouvelle configuration dans nginx.conf
    with open(NGINX_OUTPUT, 'w') as f:
        f.write(new_config)

# RÃ©cupÃ©ration des statistiques RTMP depuis l'URL stat
def get_nginx_stats():
    try:
        r = requests.get('http://localhost:8080/stat')  # VÃ©rifie que cette URL retourne les bonnes stats
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            
            # Extraction des diffÃ©rentes valeurs
            # Version Nginx : RÃ©cupÃ©rer Ã  partir de la balise <nginx_version>
            nginx_version = root.find('nginx_version').text if root.find('nginx_version') is not None else 'N/A'
            
            # Version RTMP : RÃ©cupÃ©rer Ã  partir de la balise <nginx_rtmp_version>
            rtmp_version = root.find('nginx_rtmp_version').text if root.find('nginx_rtmp_version') is not None else 'N/A'

            # Version Debian ou Ubuntu : Extraire la version entre parenthÃ¨ses dans <compiler>
            compiler = root.find('compiler').text if root.find('compiler') is not None else 'N/A'
            debian_version = "N/A"
            match = re.search(r'\((.*?)\)', compiler)
            if match:
                debian_version = match.group(1)

            uptime = root.find('uptime').text if root.find('uptime') is not None else '0'
            bw_in = root.find('bw_in').text if root.find('bw_in') is not None else '0'
            bw_out = root.find('bw_out').text if root.find('bw_out') is not None else '0'
            bytes_in = root.find('bytes_in').text if root.find('bytes_in') is not None else '0'
            bytes_out = root.find('bytes_out').text if root.find('bytes_out') is not None else '0'

            # Pour les clients live et stat, les balises peuvent Ãªtre imbriquÃ©es dans une application spÃ©cifique
            nclients_live = root.find('.//application[name="live"]/live/nclients')
            nclients_stat = root.find('.//application[name="stat"]/live/nclients')
            
            # Nous utilisons `.text` si les balises existent
            nclients_live = nclients_live.text if nclients_live is not None else '0'
            nclients_stat = nclients_stat.text if nclients_stat is not None else '0'

            # Convertir uptime en format lisible (jours, heures, minutes, secondes)
            uptime_readable = convert_uptime(int(uptime))

            # Retourner les statistiques sous forme de dictionnaire
            stats = {
                'nginx_version': nginx_version,
                'rtmp_version': rtmp_version,
                'uptime': uptime_readable,
                'bw_in': bw_in,
                'bw_out': bw_out,
                'bytes_in': bytes_in,
                'bytes_out': bytes_out,
                'nclients_live': nclients_live,
                'nclients_stat': nclients_stat,
                'debian_version': debian_version,
            }
            return stats
        else:
            return "Erreur lors de la rÃ©cupÃ©ration des statistiques."
    except Exception as e:
        return f"Erreur : {str(e)}"

@app.route('/api/stats')
@login_required
def api_stats():
    stats = get_nginx_stats()
    if isinstance(stats, dict):
        return jsonify(success=True, data=stats)
    else:
        return jsonify(success=False, error=stats), 500
		
# Convertit des secondes en texte lisible : 1y 2mo 3d 4h 5m 6s
def convert_uptime(seconds):
    """ Convertir l'uptime en format lisible avec des abrÃ©viations """
    years = seconds // 31536000
    months = (seconds % 31536000) // 2592000
    days = (seconds % 2592000) // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    readable = []
    if years > 0:
        readable.append(f"{years}y")
    if months > 0:
        readable.append(f"{months}mo")
    if days > 0:
        readable.append(f"{days}d")
    if hours > 0:
        readable.append(f"{hours}h")
    if minutes > 0:
        readable.append(f"{minutes}m")
    if seconds > 0 or len(readable) == 0:
        readable.append(f"{seconds}s")
    
    return " ".join(readable)

@app.route('/toggle/<platform>', methods=['POST'])
@login_required
def toggle_platform(platform):
    config = load_config()
    if platform in config['outputs']:
        # Bascule l'Ã©tat actuel de la plateforme (enabled <-> disabled)
        current_status = config['outputs'][platform].get('enabled', False)
        config['outputs'][platform]['enabled'] = not current_status
        save_config(config)
        update_nginx_config(config)
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'])

        # Retourne le nouvel Ã©tat sous forme JSON pour l'AJAX
        return jsonify({
            "success": True,
            "enabled": config['outputs'][platform]['enabled']
        })
    else:
        return jsonify({
            "success": False,
            "error": "Platform not found"
        }), 404
		
# bouton sauvegarder
@app.route('/update/<name>', methods=['POST'])
@login_required
def update_platform(name):
    new_name = request.form.get('name')
    new_url = request.form.get('url')
    new_key = request.form.get('key')
    
    config = load_config()
    if name in config['outputs']:
        # Update the platform details
        config['outputs'][name]['url'] = new_url
        config['outputs'][name]['key'] = new_key
        if new_name != name:
            # Rename the platform if the name has changed
            config['outputs'][new_name] = config['outputs'].pop(name)
        save_config(config)
        update_nginx_config(config)
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'])
    
    return redirect('/')

# Lancement de l'application en mode debug et accessible sur le rÃ©seau local
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
