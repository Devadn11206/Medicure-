from sqlalchemy import Column, Integer, String, Float
from .database import Base

class Medicine(Base):
    __tablename__ = "medicines"

    id = Column(Integer, primary_key=True, index=True)
    medicine_name = Column(String, index=True, nullable=False)
    active_ingredient = Column(String, index=True, nullable=False)
    generic_name = Column(String, nullable=False)
    brand_price = Column(Float, nullable=False)
    generic_price = Column(Float, nullable=False)
    category = Column(String)
