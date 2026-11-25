bind = "0.0.0.0:8000"
workers = 2
threads = 2
timeout = 180
graceful_timeout = 30
umask = 0o077
accesslog = "-"
errorlog = "-"

# The application validates the original socket peer before accepting any forwarded headers.
forwarded_allow_ips = ""
secure_scheme_headers = {}
