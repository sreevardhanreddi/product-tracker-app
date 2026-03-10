from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/")
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/products/{product_id}")
def product_detail(request: Request, product_id: int):
    return templates.TemplateResponse(
        "product_detail.html",
        {"request": request, "product_id": product_id},
    )
