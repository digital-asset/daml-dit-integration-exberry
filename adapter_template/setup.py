from setuptools import setup

setup(name='exberry-adapter',
      version='0.1.16',
      description='Exchange Adapter Starter Tempalte',
      author='Digital Asset',
      url='daml.com',
      license='Apache2',
      install_requires=['dazl>=7,<8', 'aiohttp'],
      packages=['bot'],
      include_package_data=True)
