#!/usr/bin/python3
"""CGI entrypoint; protect this location with the web server's Nagios authentication."""
import runpy

runpy.run_path('/usr/local/libexec/nagios-rrd-web.cgi', run_name='__main__')
