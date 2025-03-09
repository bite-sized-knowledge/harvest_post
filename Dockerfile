# Use the aws python3.12 lambda image 
FROM public.ecr.aws/lambda/python:3.12

# Install the function's dependencies using file requirements.txt
# from your project folder.
COPY requirements.txt  .
RUN pip3 install -r requirements.txt

# Copy function code
COPY src/ ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler
CMD [ "main.lambda_handler" ]