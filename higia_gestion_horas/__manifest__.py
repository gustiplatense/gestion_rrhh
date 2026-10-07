# -*- coding: utf-8 -*-
{
    'name': 'HIGIA - Gestion de Horas (Portal Gerentes)',
    'version': '19.0.1.0.3',
    'category': 'Human Resources/Attendances',
    'summary': 'Web app portal para gestion de asistencias, horas extras y novedades por gerentes',
    'description': 'AOO Para supervisores',
    'author': 'Digitalo Consulting SRL',
    'company': 'Digitalo Consulting SRL',
    'maintainer': 'Digitalo Consulting SRL',
    'website': 'https://digitaloconsulting.com',
    'license': 'OPL-1',
    'depends': [
        'hr',
        'hr_attendance',
        'hr_holidays',
        'portal',
    ],
    'data': [
        'security/security.xml',
        'views/templates.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
}
