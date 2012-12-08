# -*- coding: utf-8 -*-

"""
Copyright (C) 2010 Dariusz Suchojad <dsuch at gefira.pl>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

# stdlib
import os, json, shutil, uuid
from copy import deepcopy
from random import getrandbits
from traceback import format_exc

# Django
from django.core.management import call_command# Zato
# TODO: There really shouldn't be any direct dependency between zato-cli and zato-admin
from zato.admin.zato_settings import update_globals 

from zato.cli import get_tech_account_opts, common_logging_conf_contents, common_odb_opts, ZatoCommand
from zato.common.defaults import zato_admin_host, zato_admin_port
from zato.common.markov_passwords import generate_password
from zato.common.util import encrypt

config_template = """{{
  "host": "{host}",
  "port": {port},
  "db_type": "{db_type}",
  "log_config": "./config/repo/{log_config}",

  "DEBUG": 1,

  "DATABASE_NAME": "{DATABASE_NAME}",
  "DATABASE_USER": "{DATABASE_USER}",
  "DATABASE_PASSWORD": "{DATABASE_PASSWORD}",
  "DATABASE_HOST": "{DATABASE_HOST}",
  "DATABASE_PORT": "{DATABASE_PORT}",

  "TIME_ZONE": "America/New_York",
  "LANGUAGE_CODE": "en-us",

  "SITE_ID": {SITE_ID},
  "SECRET_KEY": "{SECRET_KEY}",
  
  "TECH_ACCOUNT_NAME": "{TECH_ACCOUNT_NAME}",
  "TECH_ACCOUNT_PASSWORD": "{TECH_ACCOUNT_PASSWORD}"
}}
"""

initial_data_json = """[{{
"pk": {SITE_ID},
"model": "sites.site",
"fields": {{
    "name": "Zato admin",
    "domain":"zatoadmin.example.com"
    }}
}}]
"""

class Create(ZatoCommand):
    """ Creates a new Zato Admin web console
    """
    needs_empty_dir = True
    allow_empty_secrets = True
    
    opts = deepcopy(common_odb_opts)
    
    opts.append({'name':'pub_key_path', 'help':"Path to the Zato Admin's public key in PEM"})
    opts.append({'name':'priv_key_path', 'help':"Path to the Zato Admin's private key in PEM"})
    opts.append({'name':'cert_path', 'help':"Path to the Zato Admin's certificate in PEM"})
    opts.append({'name':'ca_certs_path', 'help':"Path to a bundle of CA certificates to be trusted"})
    
    opts += get_tech_account_opts()
    
    def __init__(self, args):
        self.target_dir = os.path.abspath(args.path)
        super(Create, self).__init__(args)

    def execute(self, args, show_output=True, password=None):
        os.chdir(self.target_dir)

        repo_dir = os.path.join(self.target_dir, 'config', 'repo')
        zato_admin_conf_path = os.path.join(repo_dir, 'zato-admin.conf')
        initial_data_json_path = os.path.join(repo_dir, 'initial-data.json')

        os.mkdir(os.path.join(self.target_dir, 'logs'))
        os.mkdir(os.path.join(self.target_dir, 'config'))
        os.mkdir(os.path.join(self.target_dir, 'config', 'zdaemon'))
        os.mkdir(repo_dir)
        
        user_name = 'admin'
        password = password if password else generate_password()
        
        for attr, name in (('pub_key_path', 'pub-key'), ('priv_key_path', 'priv-key'), ('cert_path', 'cert'), ('ca_certs_path', 'ca-certs')):
            file_name = os.path.join(repo_dir, 'zato-admin-{}.pem'.format(name))
            shutil.copyfile(os.path.abspath(getattr(args, attr)), file_name)
        
        pub_key = open(os.path.join(repo_dir, 'zato-admin-pub-key.pem')).read()
        
        config = {
            'host': zato_admin_host,
            'port': zato_admin_port,
            'db_type': args.odb_type,
            'log_config': 'logging.conf',
            'DATABASE_NAME': args.odb_db_name,
            'DATABASE_USER': args.odb_user,
            'DATABASE_PASSWORD': encrypt(args.odb_password, pub_key),
            'DATABASE_HOST': args.odb_host,
            'DATABASE_PORT': args.odb_port,
            'SITE_ID': getrandbits(20),
            'SECRET_KEY': encrypt(uuid.uuid4().hex, pub_key),
            'TECH_ACCOUNT_NAME':args.tech_account_name,
            'TECH_ACCOUNT_PASSWORD':encrypt(args.tech_account_password, pub_key),
        }
        
        open(os.path.join(repo_dir, 'logging.conf'), 'w').write(common_logging_conf_contents.format(log_path='./logs/zato-admin.log'))
        open(zato_admin_conf_path, 'w').write(config_template.format(**config))
        open(initial_data_json_path, 'w').write(initial_data_json.format(**config))
        
        # Initial info
        self.store_initial_info(self.target_dir, self.COMPONENTS.ZATO_ADMIN.code)
        
        config = json.loads(open(os.path.join(repo_dir, 'zato-admin.conf')).read())
        config['config_dir'] = self.target_dir
        update_globals(config, self.target_dir)
        
        os.environ['DJANGO_SETTINGS_MODULE'] = 'zato.admin.settings'
        
        # Can't import these without DJANGO_SETTINGS_MODULE being set
        from django.contrib.auth.models import User
        from django.db import connection
        from django.db.utils import IntegrityError
        
        call_command('syncdb', interactive=False, verbosity=0)
        call_command('loaddata', initial_data_json_path, verbosity=0)
        
        try:
            call_command('createsuperuser', interactive=False, username=user_name, first_name='admin-first-name',
                                     last_name='admin-last-name', email='admin@invalid.example.com')
            admin_created = True
        except IntegrityError, e:
            admin_created = False
            connection._rollback()
            msg = 'Ignoring IntegrityError e:[{}]'.format(format_exc(e))
            self.logger.info(msg)
            
        user = User.objects.get(username=user_name)
        user.set_password(password)
        user.save()

        if show_output:
            if self.verbose:
                msg = """Successfully created a Zato Admin instance.
    You can start it with the 'zato start {path}' command.""".format(path=os.path.abspath(os.path.join(os.getcwd(), self.target_dir)))
                self.logger.debug(msg)
            else:
                self.logger.info('OK')

        return admin_created